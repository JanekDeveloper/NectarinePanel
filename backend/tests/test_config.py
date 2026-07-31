"""Backend configuration parsing tests."""

import pytest
from app.core.config import Settings
from cryptography.fernet import Fernet
from pydantic import ValidationError


def test_empty_telegram_owner_id_disables_integration() -> None:
    """Empty Telegram owner ID values are parsed as disabled integration."""
    settings = Settings(
        environment="test",
        jwt_secret="test-secret-that-is-at-least-thirty-two-characters",
        telegram_owner_id="",
    )

    assert settings.telegram_owner_id is None


def production_settings(**overrides: str) -> dict[str, str]:
    """Return valid production settings with optional test overrides."""
    values = {
        "environment": "production",
        "jwt_secret": "production-jwt-secret-with-at-least-32-characters",
        "agent_token": "production-agent-token-with-at-least-32-characters",
        "telegram_api_token": "production-telegram-token-with-at-least-32-characters",
        "field_encryption_key": Fernet.generate_key().decode("ascii"),
    }
    values.update(overrides)
    return values


def test_production_requires_field_encryption_key() -> None:
    """Reject production startup when persisted secrets would be plaintext."""
    with pytest.raises(ValidationError, match="FIELD_ENCRYPTION_KEY is required"):
        Settings(**production_settings(field_encryption_key=""))


def test_production_rejects_malformed_field_encryption_key() -> None:
    """Reject production startup with a malformed Fernet key."""
    with pytest.raises(ValidationError, match="valid Fernet key"):
        Settings(**production_settings(field_encryption_key="not-a-fernet-key"))


def test_production_accepts_valid_secrets() -> None:
    """Accept a complete production secret configuration."""
    settings = Settings(**production_settings())

    assert settings.environment == "production"
