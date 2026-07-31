"""User and project membership API schemas."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

UserRoleLiteral = Literal["owner", "admin", "maintainer", "viewer"]
ProjectMemberRoleLiteral = Literal["maintainer", "viewer"]


class UserCreate(BaseModel):
    """Create a local panel user."""

    username: str = Field(min_length=3, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$")
    password: str = Field(min_length=12, max_length=256)
    display_name: str | None = Field(default=None, max_length=120)
    role: UserRoleLiteral = "viewer"


class UserUpdate(BaseModel):
    """Update safe mutable user account fields."""

    display_name: str | None = Field(default=None, max_length=120)
    role: UserRoleLiteral | None = None
    password: str | None = Field(default=None, min_length=12, max_length=256)


class UserResponse(BaseModel):
    """Safe user account representation."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    username: str
    display_name: str | None
    role: str
    is_active: bool
    disabled_at: datetime | None
    last_login_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ProjectMembershipUpsert(BaseModel):
    """Create or update one project membership."""

    role: ProjectMemberRoleLiteral


class ProjectMembershipResponse(BaseModel):
    """Safe project membership representation."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    user_id: str
    role: str
    created_at: datetime
    updated_at: datetime
    user: UserResponse
