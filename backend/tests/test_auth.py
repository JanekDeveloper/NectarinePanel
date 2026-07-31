"""Authentication API tests."""

import asyncio

import pytest
from app.models.entities import SystemSetting, TelegramAccount
from conftest import TestSession
from fastapi.testclient import TestClient
from sqlalchemy import select


def test_login_and_refresh(client: TestClient) -> None:
    """A valid owner can log in and rotate a refresh token."""
    login = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "correct-horse-battery"},
    )
    assert login.status_code == 200
    tokens = login.json()
    refresh = client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": tokens["refresh_token"]},
    )
    assert refresh.status_code == 200
    assert refresh.json()["refresh_token"] != tokens["refresh_token"]


def test_invalid_login_is_rejected(client: TestClient) -> None:
    """Wrong credentials do not reveal whether the user exists."""
    response = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "wrong-password-value"},
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid credentials"


def test_refresh_token_reuse_revokes_the_session(client: TestClient) -> None:
    """Reusing a rotated refresh token revokes all active tokens for the owner."""
    login = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "correct-horse-battery"},
    ).json()
    rotated = client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": login["refresh_token"]},
    )
    assert rotated.status_code == 200
    reused = client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": login["refresh_token"]},
    )
    assert reused.status_code == 401
    assert reused.json()["detail"] == "Refresh token reuse detected"
    revoked = client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": rotated.json()["refresh_token"]},
    )
    assert revoked.status_code == 401


def test_telegram_two_factor_login_flow(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Enabled Telegram 2FA requires bot approval before issuing tokens."""
    delivered: list[str] = []

    async def fake_delivery(
        bot_token: str,
        owner_id: int,
        challenge_token: str,
        request_ip: str,
    ) -> None:
        """Capture the challenge instead of calling Telegram."""
        del request_ip
        assert bot_token == "123:token"
        assert owner_id == 6_755_406_722
        delivered.append(challenge_token)

    async def configure_two_factor() -> None:
        """Enable Telegram two-factor settings in the test database."""
        async with TestSession() as session:
            session.add_all(
                [
                    SystemSetting(
                        key="telegram.bot_token",
                        value="123:token",
                        is_secret=True,
                    ),
                    SystemSetting(
                        key="telegram.owner_id",
                        value="6755406722",
                        is_secret=False,
                    ),
                    SystemSetting(
                        key="telegram.two_factor_enabled",
                        value="true",
                        is_secret=False,
                    ),
                ]
            )
            await session.commit()

    monkeypatch.setattr("app.api.routes.auth._send_telegram_approval", fake_delivery)
    asyncio.run(configure_two_factor())
    login = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "correct-horse-battery"},
    )
    assert login.status_code == 202
    challenge_token = login.json()["challenge_token"]
    assert delivered == [challenge_token]
    pending = client.post(
        "/api/v1/auth/2fa/complete",
        json={"challenge_token": challenge_token},
    )
    assert pending.status_code == 409
    approved = client.post(
        f"/api/v1/telegram/2fa/{challenge_token}/approve",
        headers={"X-Telegram-Service-Token": "development-telegram-service-token"},
        json={"telegram_user_id": 6_755_406_722, "username": "owner"},
    )
    assert approved.status_code == 200
    completed = client.post(
        "/api/v1/auth/2fa/complete",
        json={"challenge_token": challenge_token},
    )
    assert completed.status_code == 200
    assert completed.json()["access_token"]
    replay = client.post(
        "/api/v1/auth/2fa/complete",
        json={"challenge_token": challenge_token},
    )
    assert replay.status_code == 410


def test_telegram_owner_binding_accepts_64_bit_user_id(client: TestClient) -> None:
    """Telegram owner bindings support IDs larger than signed 32-bit integers."""

    async def configure_owner() -> None:
        """Configure a Telegram owner with a 64-bit identifier."""
        async with TestSession() as session:
            session.add(
                SystemSetting(
                    key="telegram.owner_id",
                    value="6755406722",
                    is_secret=False,
                )
            )
            await session.commit()

    async def stored_owner_id() -> int | None:
        """Return the persisted Telegram owner identifier."""
        async with TestSession() as session:
            account = await session.scalar(select(TelegramAccount))
            return account.telegram_user_id if account else None

    asyncio.run(configure_owner())
    response = client.post(
        "/api/v1/telegram/bind",
        headers={"X-Telegram-Service-Token": "development-telegram-service-token"},
        json={"telegram_user_id": 6_755_406_722, "username": "owner"},
    )

    assert response.status_code == 200
    assert asyncio.run(stored_owner_id()) == 6_755_406_722
