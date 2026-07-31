"""Managed database API schemas."""

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class DatabaseEngine(StrEnum):
    """Supported managed database engines."""

    POSTGRESQL = "postgresql"
    MYSQL = "mysql"
    MARIADB = "mariadb"
    REDIS = "redis"
    VALKEY = "valkey"
    SQLITE = "sqlite"


class DatabaseServerCreate(BaseModel):
    """Database administration connection."""

    name: str = Field(min_length=2, max_length=120)
    engine: DatabaseEngine
    host: str = Field(min_length=1, max_length=253)
    port: int = Field(ge=0, le=65535)
    admin_username: str = Field(default="", max_length=255)
    admin_password: str = Field(default="", max_length=4096)
    tls_enabled: bool = True

    @field_validator("host", "admin_username")
    @classmethod
    def reject_control_characters(cls, value: str) -> str:
        """Reject values unsafe for logs and downstream clients."""
        if any(ord(character) < 32 for character in value):
            raise ValueError("Control characters are not allowed")
        return value

    @model_validator(mode="after")
    def validate_engine_connection(self) -> "DatabaseServerCreate":
        """Validate network credentials or the local SQLite sentinel."""
        if self.engine == DatabaseEngine.SQLITE:
            if self.host != "local" or self.port != 0 or self.tls_enabled:
                raise ValueError("SQLite server must use host=local, port=0, and no TLS")
            return self
        if self.port == 0 or not self.admin_username or not self.admin_password:
            raise ValueError("Network database credentials and port are required")
        return self


class DatabaseServerResponse(BaseModel):
    """Database server metadata without credentials."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    engine: str
    host: str
    port: int
    admin_username: str
    tls_enabled: bool
    created_at: datetime
    updated_at: datetime


class DatabaseInstanceCreate(BaseModel):
    """Managed database creation request."""

    server_id: str
    project_id: str | None = None
    name: str = Field(pattern=r"^[a-zA-Z_][a-zA-Z0-9_]{0,62}$")
    username: str = Field(default="", pattern=r"^(?:[a-zA-Z_][a-zA-Z0-9_]{0,62})?$")
    attach_environment: bool = True


class DatabaseInstanceResponse(BaseModel):
    """Managed database metadata without its password."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    server_id: str
    project_id: str | None
    name: str
    username: str
    engine: str
    status: str
    created_at: datetime
    updated_at: datetime


class DatabaseTableResponse(BaseModel):
    """Basic managed database table metadata."""

    model_config = ConfigDict(populate_by_name=True)

    schema_name: str | None = Field(default=None, alias="schema")
    name: str
    table_type: str = "table"


class DatabaseInstanceCreated(BaseModel):
    """One-time database credentials returned at creation."""

    database: DatabaseInstanceResponse
    password: str | None
    connection_string: str
    job_id: str


class DatabaseImportConfirmation(BaseModel):
    """Database overwrite confirmation supplied as multipart form data."""

    confirm_database_name: str = Field(min_length=1, max_length=63)


class DatabaseDeleteRequest(BaseModel):
    """Exact database name confirmation for destructive deletion."""

    confirm_database_name: str = Field(min_length=1, max_length=63)


class DatabaseServerStatus(BaseModel):
    """Bounded database server health and version information."""

    engine: str
    status: str
    version: str | None = None
    details: dict[str, str | int | float | bool | None] = Field(default_factory=dict)
