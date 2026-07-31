"""Telegram bot environment configuration."""

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class BotSettings(BaseSettings):
    """Validated Telegram integration settings."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    telegram_bot_token: str = ""
    telegram_owner_id: int | None = None
    backend_internal_url: str = "http://backend:8000/api/v1"
    telegram_api_token: str = Field(default="", alias="TELEGRAM_API_TOKEN")
    telegram_max_file_bytes: int = 45 * 1024 * 1024

    @field_validator("telegram_owner_id", mode="before")
    @classmethod
    def parse_optional_telegram_owner_id(cls, value: object) -> object:
        """Treat an empty Telegram owner ID as disabled integration."""
        if value == "":
            return None
        return value


settings = BotSettings()
