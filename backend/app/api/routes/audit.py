"""Audit log read API."""

from fastapi import APIRouter, Query
from sqlalchemy import select

from app.api.dependencies import CurrentUser, DbSession
from app.models.entities import AuditLog
from app.schemas.common import AuditLogResponse

router = APIRouter(prefix="/audit-logs", tags=["audit"])


@router.get("", response_model=list[AuditLogResponse])
async def list_audit_logs(
    user: CurrentUser,
    session: DbSession,
    limit: int = Query(default=100, ge=1, le=500),
) -> list[AuditLog]:
    """List recent audit events without sensitive payloads."""
    del user
    events = await session.scalars(
        select(AuditLog).order_by(AuditLog.created_at.desc()).limit(limit)
    )
    return list(events)
