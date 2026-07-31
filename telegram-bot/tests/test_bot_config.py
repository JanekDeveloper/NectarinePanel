"""Telegram bot configuration parsing tests."""

from nectarine_bot.config import BotSettings
from nectarine_bot.config import settings as runtime_settings
from nectarine_bot.main import apply_runtime_config


def test_empty_telegram_owner_id_disables_integration() -> None:
    """Empty Telegram owner ID values are parsed as disabled integration."""
    settings = BotSettings(telegram_owner_id="")

    assert settings.telegram_owner_id is None


def test_persisted_runtime_configuration_enables_polling() -> None:
    """Persisted settings can activate a service started without environment credentials."""
    original = (
        runtime_settings.telegram_bot_token,
        runtime_settings.telegram_owner_id,
        runtime_settings.telegram_max_file_bytes,
    )
    try:
        configured = apply_runtime_config(
            {
                "bot_token": "123456:abcdefghijklmnopqrstuvwxyz",
                "owner_id": 6_755_406_722,
                "max_file_bytes": 1024,
            }
        )

        assert configured is True
        assert runtime_settings.telegram_owner_id == 6_755_406_722
        assert runtime_settings.telegram_max_file_bytes == 1024
    finally:
        (
            runtime_settings.telegram_bot_token,
            runtime_settings.telegram_owner_id,
            runtime_settings.telegram_max_file_bytes,
        ) = original
