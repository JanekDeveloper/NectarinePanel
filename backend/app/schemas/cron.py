"""Project cron job schemas."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class CronJobCreate(BaseModel):
    """Scheduled container command."""

    name: str = Field(min_length=2, max_length=120)
    expression: str = Field(min_length=5, max_length=120)
    command: str = Field(min_length=1, max_length=4096)
    enabled: bool = True


class CronJobResponse(BaseModel):
    """Cron job metadata without command output."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    name: str
    expression: str
    command: str
    enabled: bool
    last_run_at: datetime | None
    last_status: str | None
    created_at: datetime
    updated_at: datetime
