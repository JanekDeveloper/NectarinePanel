"""Panel owner settings with encrypted secret storage."""

import re

from fastapi import APIRouter, HTTPException
from sqlalchemy import select, update

from app.api.dependencies import AppSettings, CurrentUser, DbSession
from app.core.security import SecretCipher
from app.models.entities import RefreshToken, SystemSetting, UserRole
from app.schemas.common import MessageResponse
from app.schemas.settings import (
    PanelSettingsStatus,
    SettingResponse,
    SettingValue,
    TelegramStatus,
)
from app.services.audit import write_audit_log
from app.services.permissions import require_global_role
from app.services.system_settings import (
    alert_thresholds,
    telegram_runtime_settings,
    telegram_two_factor_enabled,
)

router = APIRouter(prefix="/settings", tags=["settings"])

SETTING_POLICY: dict[str, bool] = {
    "panel.locale": False,
    "telegram.owner_id": False,
    "telegram.bot_token": True,
    "telegram.two_factor_enabled": False,
    "cloudflare.api_token": True,
    "alerts.disk_percent": False,
    "alerts.memory_percent": False,
}
TELEGRAM_TOKEN_PATTERN = re.compile(r"^[0-9]{6,15}:[A-Za-z0-9_-]{20,}$")


def _validate_setting(key: str, value: str) -> None:
    """Validate one allowlisted setting according to its runtime contract."""
    if key == "panel.locale" and value not in {"ru", "en", "uk", "pl"}:
        raise HTTPException(status_code=422, detail="Locale must be ru, en, uk, or pl")
    if key == "telegram.owner_id":
        try:
            owner_id = int(value)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="Telegram owner ID is invalid") from exc
        if not 1 <= owner_id <= 9_223_372_036_854_775_807:
            raise HTTPException(status_code=422, detail="Telegram owner ID is invalid")
    if key == "telegram.bot_token" and not TELEGRAM_TOKEN_PATTERN.fullmatch(value):
        raise HTTPException(status_code=422, detail="Telegram bot token is invalid")
    if key == "telegram.two_factor_enabled" and value not in {"true", "false"}:
        raise HTTPException(status_code=422, detail="Two-factor value must be true or false")
    if key == "cloudflare.api_token" and not 20 <= len(value) <= 8192:
        raise HTTPException(status_code=422, detail="Cloudflare token is invalid")


@router.get("", response_model=list[SettingResponse])
async def list_settings(user: CurrentUser, session: DbSession) -> list[SettingResponse]:
    """List known stored settings with masked secret values."""
    require_global_role(user, UserRole.OWNER)
    records = await session.scalars(select(SystemSetting).order_by(SystemSetting.key))
    return [
        SettingResponse(
            key=record.key,
            value="••••••••" if record.is_secret else record.value,
            is_secret=record.is_secret,
            updated_at=record.updated_at,
        )
        for record in records
    ]


@router.get("/status", response_model=PanelSettingsStatus)
async def panel_settings_status(
    user: CurrentUser,
    session: DbSession,
    settings: AppSettings,
) -> PanelSettingsStatus:
    """Return effective non-secret settings and secret configuration state."""
    require_global_role(user, UserRole.OWNER)
    records = {
        record.key: record
        for record in await session.scalars(
            select(SystemSetting).where(
                SystemSetting.key.in_({"panel.locale", "cloudflare.api_token"})
            )
        )
    }
    disk, memory = await alert_thresholds(session, settings)
    locale_record = records.get("panel.locale")
    locale = (
        locale_record.value
        if locale_record and locale_record.value in {"ru", "en", "uk", "pl"}
        else "ru"
    )
    return PanelSettingsStatus(
        locale=locale,
        disk_alert_percent=disk,
        memory_alert_percent=memory,
        cloudflare_configured="cloudflare.api_token" in records,
    )


@router.put("/{key}", response_model=SettingResponse)
async def put_setting(
    key: str,
    data: SettingValue,
    user: CurrentUser,
    session: DbSession,
    settings: AppSettings,
) -> SettingResponse:
    """Create or replace one allowlisted setting."""
    require_global_role(user, UserRole.OWNER)
    if key not in SETTING_POLICY:
        raise HTTPException(status_code=404, detail="Unknown setting")
    _validate_setting(key, data.value)
    secret = SETTING_POLICY[key]
    if (
        secret
        and settings.environment not in {"development", "test"}
        and not settings.field_encryption_key
    ):
        raise HTTPException(
            status_code=409,
            detail="FIELD_ENCRYPTION_KEY is required for secret settings",
        )
    if key in {"alerts.disk_percent", "alerts.memory_percent"}:
        try:
            threshold = float(data.value)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="Threshold must be numeric") from exc
        if not 1 <= threshold <= 100:
            raise HTTPException(status_code=422, detail="Threshold must be 1-100")
    cipher = SecretCipher(settings.field_encryption_key)
    stored_value = cipher.encrypt(data.value) if secret else data.value
    record = await session.scalar(select(SystemSetting).where(SystemSetting.key == key))
    if record is None:
        record = SystemSetting(key=key, value=stored_value, is_secret=secret)
        session.add(record)
    else:
        record.value = stored_value
        record.is_secret = secret
    await write_audit_log(
        session,
        action="settings.change",
        actor_id=user.id,
        resource_type="setting",
        resource_id=key,
        details={"secret": secret},
    )
    await session.commit()
    await session.refresh(record)
    return SettingResponse(
        key=record.key,
        value="••••••••" if secret else record.value,
        is_secret=secret,
        updated_at=record.updated_at,
    )


@router.get("/telegram/status", response_model=TelegramStatus)
async def telegram_status(
    user: CurrentUser,
    session: DbSession,
    settings: AppSettings,
) -> TelegramStatus:
    """Return Telegram setup state without revealing its token."""
    require_global_role(user, UserRole.OWNER)
    runtime = await telegram_runtime_settings(session, settings)
    return TelegramStatus(
        configured=bool(runtime.bot_token and runtime.owner_id),
        owner_id=str(runtime.owner_id) if runtime.owner_id is not None else None,
        two_factor_enabled=await telegram_two_factor_enabled(session),
    )


@router.post("/security/revoke-sessions", response_model=MessageResponse)
async def revoke_other_sessions(user: CurrentUser, session: DbSession) -> MessageResponse:
    """Revoke every active refresh token for the owner."""
    from datetime import UTC, datetime

    require_global_role(user, UserRole.OWNER)
    await session.execute(
        update(RefreshToken)
        .where(
            RefreshToken.user_id == user.id,
            RefreshToken.revoked_at.is_(None),
        )
        .values(revoked_at=datetime.now(UTC))
    )
    await write_audit_log(
        session,
        action="security.sessions_revoke",
        actor_id=user.id,
    )
    await session.commit()
    return MessageResponse(message="All refresh sessions revoked")
