"""Runtime resolution for owner-configurable panel settings."""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.security import SecretCipher
from app.models.entities import SystemSetting


@dataclass(frozen=True)
class TelegramRuntimeSettings:
    """Effective Telegram credentials and owner allowlist."""

    bot_token: str
    owner_id: int | None


async def _setting_records(
    session: AsyncSession,
    keys: set[str],
) -> dict[str, SystemSetting]:
    """Load requested settings as a key-indexed mapping."""
    records = await session.scalars(select(SystemSetting).where(SystemSetting.key.in_(keys)))
    return {record.key: record for record in records}


async def telegram_runtime_settings(
    session: AsyncSession,
    settings: Settings,
) -> TelegramRuntimeSettings:
    """Resolve Telegram settings with database values overriding environment defaults."""
    records = await _setting_records(
        session,
        {"telegram.bot_token", "telegram.owner_id"},
    )
    token = settings.telegram_bot_token
    token_record = records.get("telegram.bot_token")
    if token_record is not None:
        token = SecretCipher(settings.field_encryption_key).decrypt(token_record.value)
    owner_id = settings.telegram_owner_id
    owner_record = records.get("telegram.owner_id")
    if owner_record is not None:
        try:
            owner_id = int(owner_record.value)
        except ValueError:
            owner_id = None
    return TelegramRuntimeSettings(bot_token=token, owner_id=owner_id)


async def telegram_two_factor_enabled(session: AsyncSession) -> bool:
    """Return whether owner login approval through Telegram is enabled."""
    records = await _setting_records(session, {"telegram.two_factor_enabled"})
    record = records.get("telegram.two_factor_enabled")
    return record is not None and record.value.lower() == "true"


async def alert_thresholds(
    session: AsyncSession,
    settings: Settings,
) -> tuple[float, float]:
    """Resolve persisted disk and memory alert thresholds."""
    records = await _setting_records(
        session,
        {"alerts.disk_percent", "alerts.memory_percent"},
    )

    def value(key: str, fallback: float) -> float:
        record = records.get(key)
        if record is None:
            return fallback
        try:
            threshold = float(record.value)
        except ValueError:
            return fallback
        return threshold if 1 <= threshold <= 100 else fallback

    return (
        value("alerts.disk_percent", settings.alert_disk_percent),
        value("alerts.memory_percent", settings.alert_memory_percent),
    )
