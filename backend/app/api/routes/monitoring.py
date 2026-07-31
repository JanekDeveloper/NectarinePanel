"""Host monitoring API."""

import asyncio
import time
from datetime import UTC, datetime
from typing import Any

import psutil
from fastapi import APIRouter, HTTPException, Query, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select

from app.api.dependencies import CurrentUser, DbSession
from app.core.config import get_settings
from app.core.security import decode_access_token
from app.models.entities import MetricSample, Notification, Project, UserRole
from app.schemas.common import MessageResponse
from app.services.permissions import (
    accessible_project_ids,
    require_global_role,
    require_project_permission,
)

router = APIRouter(prefix="/monitoring", tags=["monitoring"])


class MetricSampleResponse(BaseModel):
    """Historical host or project metric sample."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str | None
    captured_at: datetime
    values: dict[str, Any]


class NotificationResponse(BaseModel):
    """Persistent alert shown in the dashboard and Telegram."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    notification_type: str
    severity: str
    title: str
    message: str
    resource_type: str | None
    resource_id: str | None
    read_at: datetime | None
    created_at: datetime


def collect_host_metrics() -> dict[str, Any]:
    """Collect inexpensive host metrics without blocking on CPU sampling."""
    memory = psutil.virtual_memory()
    disk = psutil.disk_usage("/")
    network = psutil.net_io_counters()
    return {
        "cpu_percent": psutil.cpu_percent(interval=None),
        "memory_percent": memory.percent,
        "memory_used": memory.used,
        "memory_total": memory.total,
        "disk_percent": disk.percent,
        "disk_used": disk.used,
        "disk_total": disk.total,
        "network_bytes_sent": network.bytes_sent,
        "network_bytes_received": network.bytes_recv,
        "uptime_seconds": int(time.time() - psutil.boot_time()),
    }


@router.get("")
async def host_metrics(user: CurrentUser) -> dict[str, Any]:
    """Return the current host metrics snapshot."""
    require_global_role(user, UserRole.OWNER, UserRole.ADMIN)
    return collect_host_metrics()


@router.get("/history", response_model=list[MetricSampleResponse])
async def metric_history(
    user: CurrentUser,
    session: DbSession,
    limit: int = Query(default=1440, ge=1, le=10080),
) -> list[MetricSample]:
    """Return recent host metric samples in chronological order."""
    require_global_role(user, UserRole.OWNER, UserRole.ADMIN)
    samples = await session.scalars(
        select(MetricSample)
        .where(MetricSample.project_id.is_(None))
        .order_by(MetricSample.captured_at.desc())
        .limit(limit)
    )
    return list(reversed(list(samples)))


@router.get("/projects/latest", response_model=dict[str, MetricSampleResponse | None])
async def project_metrics_latest(
    user: CurrentUser,
    session: DbSession,
) -> dict[str, MetricSample | None]:
    """Return the latest project metric sample keyed by project ID."""
    project_ids = await accessible_project_ids(session, user)
    if project_ids is None:
        project_ids = list(await session.scalars(select(Project.id)))
    latest_by_project: dict[str, MetricSample | None] = {
        project_id: None for project_id in project_ids
    }
    if not project_ids:
        return latest_by_project
    samples = await session.scalars(
        select(MetricSample)
        .where(MetricSample.project_id.in_(project_ids))
        .order_by(MetricSample.project_id.asc(), MetricSample.captured_at.desc())
    )
    for sample in samples:
        if sample.project_id is not None and latest_by_project[sample.project_id] is None:
            latest_by_project[sample.project_id] = sample
    return latest_by_project


@router.get("/projects/{project_id}/latest", response_model=MetricSampleResponse)
async def project_metric_latest(
    project_id: str,
    user: CurrentUser,
    session: DbSession,
) -> MetricSample:
    """Return the latest resource or health metric for one project."""
    await require_project_permission(session, user, project_id, "project:read")
    sample = await session.scalar(
        select(MetricSample)
        .where(MetricSample.project_id == project_id)
        .order_by(MetricSample.captured_at.desc())
    )
    if sample is None:
        raise HTTPException(status_code=404, detail="Project metrics not found")
    return sample


@router.get("/projects/{project_id}/history", response_model=list[MetricSampleResponse])
async def project_metric_history(
    project_id: str,
    user: CurrentUser,
    session: DbSession,
    limit: int = Query(default=720, ge=1, le=10080),
) -> list[MetricSample]:
    """Return recent project metric samples in chronological order."""
    await require_project_permission(session, user, project_id, "project:read")
    samples = await session.scalars(
        select(MetricSample)
        .where(MetricSample.project_id == project_id)
        .order_by(MetricSample.captured_at.desc())
        .limit(limit)
    )
    return list(reversed(list(samples)))


@router.get("/notifications", response_model=list[NotificationResponse])
async def notifications(
    user: CurrentUser,
    session: DbSession,
    unread_only: bool = False,
) -> list[Notification]:
    """List persistent operational alerts."""
    require_global_role(user, UserRole.OWNER, UserRole.ADMIN)
    query = select(Notification)
    if unread_only:
        query = query.where(Notification.read_at.is_(None))
    result = await session.scalars(query.order_by(Notification.created_at.desc()).limit(200))
    return list(result)


@router.post(
    "/notifications/{notification_id}/read",
    response_model=MessageResponse,
)
async def mark_notification_read(
    notification_id: str,
    user: CurrentUser,
    session: DbSession,
) -> MessageResponse:
    """Mark one alert as read."""
    require_global_role(user, UserRole.OWNER, UserRole.ADMIN)
    notification = await session.get(Notification, notification_id)
    if notification is None:
        raise HTTPException(status_code=404, detail="Notification not found")
    notification.read_at = datetime.now(UTC)
    await session.commit()
    return MessageResponse(message="Notification marked as read")


@router.websocket("/live")
async def live_monitoring(
    websocket: WebSocket,
    token: str = Query(min_length=20),
) -> None:
    """Stream current host metrics snapshots over WebSocket."""
    settings = get_settings()
    try:
        decode_access_token(token, settings)
    except Exception:
        await websocket.close(code=4401, reason="Unauthorized")
        return
    await websocket.accept()
    try:
        while True:
            await websocket.send_json(collect_host_metrics())
            await asyncio.sleep(2)
    except WebSocketDisconnect:
        return
    except Exception:
        return
