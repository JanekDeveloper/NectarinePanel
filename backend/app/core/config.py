"""Application configuration loaded from environment variables."""

from functools import lru_cache
from pathlib import Path
from typing import Annotated

from cryptography.fernet import Fernet
from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    """Validated runtime settings for the backend."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = "NectarinePanel"
    environment: str = "development"
    api_prefix: str = "/api/v1"
    database_url: str = "sqlite+aiosqlite:///./nectarine.db"
    redis_url: str = "redis://localhost:6379/0"
    jwt_secret: str = "development-only-change-me"  # noqa: S105 - local development only
    jwt_algorithm: str = "HS256"
    access_token_minutes: int = 15
    refresh_token_days: int = 30
    login_attempt_limit: int = 5
    login_attempt_window_seconds: int = 300
    field_encryption_key: str | None = None
    agent_url: str = "http://127.0.0.1:8090"
    agent_token: str = "development-agent-token"  # noqa: S105 - local development only
    public_ip: str | None = None
    letsencrypt_email: str | None = None
    telegram_api_token: str = "development-telegram-service-token"  # noqa: S105 - local development only
    telegram_bot_token: str = ""
    telegram_owner_id: int | None = None
    telegram_max_file_bytes: int = 45 * 1024 * 1024
    public_base_url: str = "http://localhost:8000"
    storage_root: Path = Path("/srv/vps-panel")
    config_root: Path = Path("/etc/nectarine-panel")
    nginx_config_root: Path = Path("/etc/nginx/vps-panel")
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:3000"]
    )
    max_upload_bytes: int = 512 * 1024 * 1024
    alert_disk_percent: float = 90
    alert_memory_percent: float = 90

    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_cors_origins(cls, value: object) -> object:
        """Parse a comma-separated CORS origin list."""
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @field_validator("telegram_owner_id", mode="before")
    @classmethod
    def parse_optional_telegram_owner_id(cls, value: object) -> object:
        """Treat an empty Telegram owner ID as disabled integration."""
        if value == "":
            return None
        return value

    @field_validator("jwt_secret")
    @classmethod
    def validate_jwt_secret(cls, value: str) -> str:
        """Reject malformed custom JWT secrets."""
        if len(value) < 32 and value != "development-only-change-me":
            raise ValueError("JWT_SECRET must contain at least 32 characters")
        return value

    @model_validator(mode="after")
    def validate_production_secrets(self) -> "Settings":
        """Reject development credentials outside local environments."""
        if self.environment.lower() in {"development", "test"}:
            return self
        required_secrets = {
            "JWT_SECRET": (self.jwt_secret, "development-only-change-me"),
            "AGENT_TOKEN": (self.agent_token, "development-agent-token"),
            "TELEGRAM_API_TOKEN": (
                self.telegram_api_token,
                "development-telegram-service-token",
            ),
        }
        for name, (value, development_value) in required_secrets.items():
            if value == development_value or len(value) < 32:
                raise ValueError(f"{name} must be a unique secret with at least 32 characters")
        if not self.field_encryption_key:
            raise ValueError("FIELD_ENCRYPTION_KEY is required outside local environments")
        try:
            Fernet(self.field_encryption_key.encode("ascii"))
        except (ValueError, UnicodeEncodeError) as exc:
            raise ValueError("FIELD_ENCRYPTION_KEY must be a valid Fernet key") from exc
        return self


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide validated settings instance."""
    return Settings()
