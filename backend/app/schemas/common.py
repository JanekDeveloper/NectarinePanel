"""Shared API response schemas."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class MessageResponse(BaseModel):
    """Simple operation result."""

    message: str


class AuditLogResponse(BaseModel):
    """Security-safe audit event."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    actor_id: str | None
    action: str
    resource_type: str | None
    resource_id: str | None
    ip_address: str | None
    details: dict[str, Any]
    created_at: datetime
