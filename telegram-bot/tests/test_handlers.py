"""Telegram authorization tests."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from nectarine_bot.config import settings
from nectarine_bot.handlers import approve_two_factor, is_owner


def test_owner_allowlist() -> None:
    """Only the configured Telegram user ID is accepted."""
    original = settings.telegram_owner_id
    settings.telegram_owner_id = 12345
    try:
        assert is_owner(12345)
        assert not is_owner(54321)
        assert not is_owner(None)
    finally:
        settings.telegram_owner_id = original


@pytest.mark.asyncio
async def test_two_factor_callback_approves_login() -> None:
    """Owner callbacks approve the pending login and receive confirmation."""
    original = settings.telegram_owner_id
    settings.telegram_owner_id = 6_755_406_722
    callback = SimpleNamespace(
        from_user=SimpleNamespace(id=6_755_406_722, username="owner"),
        data="2fa:challenge",
        message=None,
        answer=AsyncMock(),
    )
    api = SimpleNamespace(approve_2fa=AsyncMock(return_value={"approved": True}))
    try:
        await approve_two_factor(callback, api)

        api.approve_2fa.assert_awaited_once_with("challenge", 6_755_406_722, "owner")
        callback.answer.assert_awaited_once_with("Вход разрешён.", show_alert=False)
    finally:
        settings.telegram_owner_id = original


@pytest.mark.asyncio
async def test_expired_two_factor_callback_is_reported() -> None:
    """Expired login challenges produce a visible Telegram alert."""
    original = settings.telegram_owner_id
    settings.telegram_owner_id = 12345
    callback = SimpleNamespace(
        from_user=SimpleNamespace(id=12345, username="owner"),
        data="2fa:expired",
        message=None,
        answer=AsyncMock(),
    )
    request = httpx.Request("POST", "http://backend/telegram/2fa/expired/approve")
    response = httpx.Response(410, request=request)
    api = SimpleNamespace(
        approve_2fa=AsyncMock(
            side_effect=httpx.HTTPStatusError(
                "expired",
                request=request,
                response=response,
            )
        )
    )
    try:
        await approve_two_factor(callback, api)

        callback.answer.assert_awaited_once_with(
            "Запрос на вход истёк. Повторите вход в панели.",
            show_alert=True,
        )
    finally:
        settings.telegram_owner_id = original
