"""Project file manager schemas."""

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class FileKind(StrEnum):
    """Creatable filesystem entry types."""

    FILE = "file"
    DIRECTORY = "directory"


class FileEntry(BaseModel):
    """Filesystem entry metadata."""

    name: str
    path: str
    kind: str
    target_kind: str | None = None
    size_bytes: int
    modified_at: datetime
    permissions: str


class FileCreateRequest(BaseModel):
    """File or directory creation request."""

    path: str = Field(min_length=1, max_length=2048)
    kind: FileKind
    content: str = Field(default="", max_length=2 * 1024 * 1024)


class FileWriteRequest(BaseModel):
    """Text file replacement request."""

    content: str = Field(max_length=2 * 1024 * 1024)


class FileMoveRequest(BaseModel):
    """Atomic file rename or move request."""

    destination: str = Field(min_length=1, max_length=2048)


class FileDeleteRequest(BaseModel):
    """Exact-path destructive action confirmation."""

    confirm_path: str = Field(min_length=1, max_length=2048)


class ArchiveRequest(BaseModel):
    """Archive creation or extraction request."""

    source: str = Field(min_length=1, max_length=2048)
    destination: str = Field(min_length=1, max_length=2048)


class FileJobResponse(BaseModel):
    """Queued file operation."""

    job_id: str
