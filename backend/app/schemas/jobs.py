"""Deployment and job API schemas."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class DeployRequest(BaseModel):
    """Manual project deployment request."""

    revision: str | None = Field(default=None, max_length=255)


class JobResponse(BaseModel):
    """Queued long-running job."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    kind: str
    status: str
    progress: int
    payload: dict[str, Any]
    result: dict[str, Any] | None
    error: str | None
    created_at: datetime
    updated_at: datetime


class DeploymentResponse(BaseModel):
    """Immutable deployment history entry."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    status: str
    source_revision: str | None
    release_path: str | None
    started_at: datetime | None
    finished_at: datetime | None
    log: str
    created_at: datetime


class DeploymentLogResponse(BaseModel):
    """Stored deployment log response."""

    deployment_id: str
    log: str


class WebhookCreateResponse(BaseModel):
    """One-time webhook token response."""

    id: str
    url: str
    token: str
    hmac_secret: str
