"""Owner-configurable runtime settings tests."""

from fastapi.testclient import TestClient


def test_runtime_settings_are_validated_and_exposed(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    """Stored settings override defaults without exposing secret values."""
    invalid = client.put(
        "/api/v1/settings/panel.locale",
        headers=auth_headers,
        json={"value": "de"},
    )
    assert invalid.status_code == 422
    values = {
        "panel.locale": "en",
        "alerts.disk_percent": "81",
        "alerts.memory_percent": "82",
        "cloudflare.api_token": "cloudflare-token-at-least-20-characters",
        "telegram.owner_id": "123456",
        "telegram.bot_token": "123456:abcdefghijklmnopqrstuvwxyz",
    }
    for key, value in values.items():
        response = client.put(
            f"/api/v1/settings/{key}",
            headers=auth_headers,
            json={"value": value},
        )
        assert response.status_code == 200
        if key in {"cloudflare.api_token", "telegram.bot_token"}:
            assert response.json()["value"] == "••••••••"
            assert value not in response.text

    status = client.get("/api/v1/settings/status", headers=auth_headers)
    assert status.status_code == 200
    assert status.json() == {
        "locale": "en",
        "disk_alert_percent": 81.0,
        "memory_alert_percent": 82.0,
        "cloudflare_configured": True,
    }
    telegram = client.get("/api/v1/settings/telegram/status", headers=auth_headers)
    assert telegram.status_code == 200
    assert telegram.json()["configured"] is True
    assert telegram.json()["owner_id"] == "123456"


def test_internal_bot_config_requires_service_auth(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    """Only the internal bot credential can read effective Telegram secrets."""
    for key, value in {
        "telegram.owner_id": "7654321",
        "telegram.bot_token": "7654321:abcdefghijklmnopqrstuvwxyz",
    }.items():
        response = client.put(
            f"/api/v1/settings/{key}",
            headers=auth_headers,
            json={"value": value},
        )
        assert response.status_code == 200
    denied = client.get("/api/v1/telegram/runtime-config")
    assert denied.status_code == 401
    allowed = client.get(
        "/api/v1/telegram/runtime-config",
        headers={"X-Telegram-Service-Token": "development-telegram-service-token"},
    )
    assert allowed.status_code == 200
    assert allowed.json() == {
        "bot_token": "7654321:abcdefghijklmnopqrstuvwxyz",
        "owner_id": 7654321,
        "max_file_bytes": 45 * 1024 * 1024,
    }


def test_panel_accepts_every_supported_locale(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    """Every locale advertised by the frontend is accepted and persisted."""
    for locale in ("ru", "en", "uk", "pl"):
        response = client.put(
            "/api/v1/settings/panel.locale",
            headers=auth_headers,
            json={"value": locale},
        )
        assert response.status_code == 200
        status = client.get("/api/v1/settings/status", headers=auth_headers)
        assert status.status_code == 200
        assert status.json()["locale"] == locale
