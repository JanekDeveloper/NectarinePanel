"""Backup, restore, and protected download schemas."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

ALLOWED_BACKUP_ITEMS = {
    "all",
    ".env",
    "current",
    "shared",
    "uploads",
    "logs",
    "world",
    "world_nether",
    "world_the_end",
    "mods",
    "config",
    "server.properties",
    "whitelist.json",
    "ops.json",
}
ALLOWED_FULL_BACKUP_ITEMS = {
    "all",
    "panel_db",
    "panel_config",
    "nginx_configs",
    "project_files",
    "minecraft_files",
    "sqlite_databases",
    "state",
}


class BackupResponse(BaseModel):
    """Backup metadata and manifest."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str | None
    database_id: str | None
    backup_type: str
    status: str
    size_bytes: int
    checksum: str | None
    manifest: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class BackupJobResponse(BaseModel):
    """Queued backup or restore operation."""

    backup: BackupResponse
    job_id: str


class BackupCreateRequest(BaseModel):
    """Manual project backup selection."""

    include_items: list[str] = Field(default_factory=lambda: ["all"], min_length=1)
    encryption_enabled: bool = False

    @field_validator("include_items")
    @classmethod
    def validate_items(cls, value: list[str]) -> list[str]:
        """Accept only known project and Minecraft backup groups."""
        unique = list(dict.fromkeys(value))
        if any(item not in ALLOWED_BACKUP_ITEMS for item in unique):
            raise ValueError("Unsupported backup item")
        if "all" in unique and len(unique) != 1:
            raise ValueError("'all' cannot be combined with explicit items")
        return unique


class FullBackupCreateRequest(BaseModel):
    """Manual full-panel backup selection."""

    include_items: list[str] = Field(default_factory=lambda: ["all"], min_length=1)
    encryption_enabled: bool = False

    @field_validator("include_items")
    @classmethod
    def validate_items(cls, value: list[str]) -> list[str]:
        """Accept only known full-panel backup groups."""
        unique = list(dict.fromkeys(value))
        if any(item not in ALLOWED_FULL_BACKUP_ITEMS for item in unique):
            raise ValueError("Unsupported full backup item")
        if "all" in unique and len(unique) != 1:
            raise ValueError("'all' cannot be combined with explicit items")
        return unique


class BackupPolicyInput(BackupCreateRequest):
    """Scheduled project backup policy input."""

    include_items: list[str] = Field(default_factory=lambda: [".env"], min_length=1)
    include_databases: bool = True
    enabled: bool = False
    schedule: str = Field(default="0 3 * * *", min_length=5, max_length=120)
    retention_count: int = Field(default=7, ge=1, le=100)


class BackupPolicyResponse(BackupPolicyInput):
    """Persisted scheduled backup policy."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    last_run_at: datetime | None
    created_at: datetime
    updated_at: datetime


class RestoreRequest(BaseModel):
    """Explicit restore confirmation."""

    confirm_project_name: str = Field(min_length=2, max_length=120)


class BackupDeleteRequest(BaseModel):
    """Exact backup identifier confirmation."""

    confirm_backup_id: str = Field(min_length=36, max_length=36)


class DownloadLinkResponse(BaseModel):
    """Expiring one-time artifact URL."""

    url: str
    expires_at: datetime
