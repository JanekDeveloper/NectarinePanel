"""Append-only audit logging service."""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import AuditLog


async def write_audit_log(
    session: AsyncSession,
    *,
    action: str,
    actor_id: str | None,
    resource_type: str | None = None,
    resource_id: str | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
    details: dict[str, Any] | None = None,
) -> AuditLog:
    """Persist an audit event without secrets."""
    event = AuditLog(
        action=action,
        actor_id=actor_id,
        resource_type=resource_type,
        resource_id=resource_id,
        ip_address=ip_address,
        user_agent=user_agent,
        details=details or {},
        created_at=datetime.now(UTC),
    )
    session.add(event)
    await session.flush()
    return event
