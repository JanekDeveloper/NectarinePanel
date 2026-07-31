"""Project and environment variable API schemas."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator, model_validator

from app.models.entities import ProjectType, RuntimeType


class ProjectBase(BaseModel):
    """Shared project fields."""

    name: str = Field(min_length=2, max_length=120)
    description: str | None = Field(default=None, max_length=5000)
    project_type: ProjectType
    runtime_type: RuntimeType
    repository_url: HttpUrl | None = None
    branch: str = Field(default="main", min_length=1, max_length=255)
    install_command: str | None = Field(default=None, max_length=4000)
    build_command: str | None = Field(default=None, max_length=4000)
    start_command: str | None = Field(default=None, max_length=4000)
    output_directory: str | None = Field(default=None, max_length=512)
    healthcheck_url: HttpUrl | None = None
    notes: str | None = Field(default=None, max_length=10000)
    runtime_config: dict[str, Any] = Field(default_factory=dict)

    @field_validator("branch", "output_directory")
    @classmethod
    def reject_control_characters(cls, value: str | None) -> str | None:
        """Reject values unsafe for subprocess and filesystem boundaries."""
        if value and any(ord(character) < 32 for character in value):
            raise ValueError("Control characters are not allowed")
        return value


class ProjectCreate(ProjectBase):
    """Project creation request."""


class ProjectTemplateResponse(BaseModel):
    """Project template preset exposed to the UI."""

    id: str
    name: str
    description: str
    project_type: ProjectType
    runtime_type: RuntimeType
    install_command: str | None = None
    build_command: str | None = None
    start_command: str | None = None
    output_directory: str | None = None
    healthcheck_url: str | None = None
    runtime_config: dict[str, Any] = Field(default_factory=dict)
    suggested_env: list[str] = Field(default_factory=list)


class ProjectFromTemplateCreate(BaseModel):
    """Create a project and materialize starter files from a template."""

    template_id: str = Field(min_length=2, max_length=64)
    name: str = Field(min_length=2, max_length=120)
    description: str | None = Field(default=None, max_length=5000)
    repository_url: HttpUrl | None = None
    branch: str = Field(default="main", min_length=1, max_length=255)

    @field_validator("branch")
    @classmethod
    def reject_control_characters(cls, value: str) -> str:
        """Reject values unsafe for subprocess boundaries."""
        if any(ord(character) < 32 for character in value):
            raise ValueError("Control characters are not allowed")
        return value


class ProjectUpdate(BaseModel):
    """Partial project update request."""

    name: str | None = Field(default=None, min_length=2, max_length=120)
    description: str | None = Field(default=None, max_length=5000)
    project_type: ProjectType | None = None
    runtime_type: RuntimeType | None = None
    repository_url: HttpUrl | None = None
    branch: str | None = Field(default=None, min_length=1, max_length=255)
    install_command: str | None = Field(default=None, max_length=4000)
    build_command: str | None = Field(default=None, max_length=4000)
    start_command: str | None = Field(default=None, max_length=4000)
    output_directory: str | None = Field(default=None, max_length=512)
    healthcheck_url: HttpUrl | None = None
    notes: str | None = Field(default=None, max_length=10000)
    runtime_config: dict[str, Any] | None = None

    @field_validator("branch", "output_directory")
    @classmethod
    def reject_control_characters(cls, value: str | None) -> str | None:
        """Reject values unsafe for subprocess and filesystem boundaries."""
        if value and any(ord(character) < 32 for character in value):
            raise ValueError("Control characters are not allowed")
        return value


class ProjectDeleteRequest(BaseModel):
    """Exact project deletion confirmation."""

    confirm_project_name: str = Field(min_length=2, max_length=120)


class ProjectResponse(BaseModel):
    """Project API representation."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    slug: str
    description: str | None
    project_type: str
    runtime_type: str
    status: str
    repository_url: str | None
    branch: str
    install_command: str | None
    build_command: str | None
    start_command: str | None
    output_directory: str | None
    healthcheck_url: str | None
    notes: str | None
    runtime_config: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class ProjectResourcePolicyInput(BaseModel):
    """Create or replace a project resource policy."""

    enabled: bool = False
    cpu_cores: float | None = Field(default=None, ge=0.1, le=128)
    memory_mb: int | None = Field(default=None, ge=64, le=1_048_576)
    disk_mb: int | None = Field(default=None, ge=64, le=10_485_760)

    @model_validator(mode="after")
    def require_limit_when_enabled(self) -> "ProjectResourcePolicyInput":
        """Require at least one configured limit for enabled policies."""
        if self.enabled and all(
            value is None for value in (self.cpu_cores, self.memory_mb, self.disk_mb)
        ):
            raise ValueError("At least one resource limit is required")
        return self


class ProjectResourcePolicyResponse(ProjectResourcePolicyInput):
    """Project resource policy API representation."""

    model_config = ConfigDict(from_attributes=True)

    id: str | None = None
    project_id: str
    created_at: datetime | None = None
    updated_at: datetime | None = None


class EnvironmentVariableInput(BaseModel):
    """Environment variable create or replace request."""

    key: str = Field(pattern=r"^[A-Z_][A-Z0-9_]{0,254}$")
    value: str = Field(max_length=65535)
    is_secret: bool = True
    reason: str | None = Field(default=None, max_length=1000)


class EnvironmentVariableResponse(BaseModel):
    """Masked environment variable representation."""

    id: str
    key: str
    value: str
    is_secret: bool
    updated_at: datetime


class EnvironmentVariableVersionResponse(BaseModel):
    """Safe environment variable history representation."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    key: str
    is_secret: bool
    value_sha256: str | None
    action: str
    actor_id: str | None
    reason: str | None
    created_at: datetime


class EnvironmentVariableRevealRequest(BaseModel):
    """Explicit confirmation required to reveal one variable value."""

    confirm_key: str = Field(pattern=r"^[A-Z_][A-Z0-9_]{0,254}$")
    reason: str | None = Field(default=None, max_length=1000)


class EnvironmentVariableRevealResponse(BaseModel):
    """One-time plaintext environment variable reveal response."""

    key: str
    value: str
    is_secret: bool


class EnvironmentVariableRollbackRequest(BaseModel):
    """Rollback request pinned to one history entry."""

    version_id: str = Field(min_length=36, max_length=36)
    confirm_key: str = Field(pattern=r"^[A-Z_][A-Z0-9_]{0,254}$")
    reason: str | None = Field(default=None, max_length=1000)


class EnvironmentImportRequest(BaseModel):
    """Bulk dotenv import request."""

    content: str = Field(min_length=1, max_length=262144)
    mode: Literal["merge", "replace"] = "merge"
    is_secret: bool = True
    reason: str | None = Field(default=None, max_length=1000)


class EnvironmentImportResponse(BaseModel):
    """Bulk dotenv import result without secret values."""

    created: int
    updated: int
    deleted: int
    keys: list[str]


class ProjectGitCredentialInput(BaseModel):
    """Private Git repository credential."""

    credential_type: Literal["token", "deploy_key"]
    value: str = Field(min_length=8, max_length=20000)
    username: str | None = Field(default=None, max_length=255)

    @field_validator("value")
    @classmethod
    def reject_invalid_secret(cls, value: str) -> str:
        """Reject malformed credential values."""
        if "\x00" in value or "\r" in value:
            raise ValueError("Credential contains unsupported control characters")
        return value


class ProjectGitCredentialResponse(BaseModel):
    """Masked Git credential status."""

    configured: bool
    credential_type: str | None = None
    username: str | None = None
