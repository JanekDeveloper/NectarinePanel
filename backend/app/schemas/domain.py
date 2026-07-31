"""Domain and certificate API schemas."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


class DomainCreate(BaseModel):
    """Domain attachment request."""

    hostname: str = Field(min_length=4, max_length=253)
    upstream_port: int | None = Field(default=None, ge=1024, le=65535)
    issue_ssl: bool = True
    is_primary: bool = False

    @field_validator("hostname")
    @classmethod
    def normalize_hostname(cls, value: str) -> str:
        """Normalize DNS names before validation by the DNS service."""
        return value.lower().rstrip(".")


class DomainResponse(BaseModel):
    """Domain state returned to the owner."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    hostname: str
    upstream_port: int
    is_primary: bool
    ssl_status: str
    certificate_expires_at: datetime | None
    created_at: datetime
    updated_at: datetime


class DomainCreateResponse(BaseModel):
    """Attached domain and queued configuration job."""

    domain: DomainResponse
    job_id: str
    resolved_addresses: list[str]


class DomainDeleteRequest(BaseModel):
    """Exact domain deletion confirmation."""

    confirm_hostname: str = Field(min_length=4, max_length=253)

    @field_validator("confirm_hostname")
    @classmethod
    def normalize_confirmation(cls, value: str) -> str:
        """Normalize the hostname while preserving exact semantic matching."""
        return value.lower().rstrip(".")


class DomainRenewRequest(BaseModel):
    """Exact confirmation for a manual certificate renewal."""

    confirm_hostname: str = Field(min_length=4, max_length=253)

    @field_validator("confirm_hostname")
    @classmethod
    def normalize_confirmation(cls, value: str) -> str:
        """Normalize the confirmed hostname before exact matching."""
        return value.lower().rstrip(".")
