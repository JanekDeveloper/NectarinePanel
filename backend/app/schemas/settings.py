"""Owner-configurable panel settings schemas."""

from datetime import datetime

from pydantic import BaseModel, Field


class SettingValue(BaseModel):
    """Validated setting replacement value."""

    value: str = Field(max_length=8192)


class SettingResponse(BaseModel):
    """Masked setting returned to the owner."""

    key: str
    value: str
    is_secret: bool
    updated_at: datetime


class TelegramStatus(BaseModel):
    """Telegram integration configuration state."""

    configured: bool
    owner_id: str | None
    two_factor_enabled: bool
    experimental_cloudflare: bool = True


class PanelSettingsStatus(BaseModel):
    """Effective non-secret owner settings used by runtime services."""

    locale: str
    disk_alert_percent: float
    memory_alert_percent: float
    cloudflare_configured: bool
