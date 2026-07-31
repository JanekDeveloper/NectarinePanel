"""User and RBAC management endpoints."""

from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Request, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from app.api.dependencies import CurrentUser, DbSession
from app.core.security import hash_password
from app.models.entities import ProjectMembership, User, UserRole
from app.schemas.common import MessageResponse
from app.schemas.user import UserCreate, UserResponse, UserUpdate
from app.services.audit import write_audit_log
from app.services.permissions import require_global_role

router = APIRouter(prefix="/users", tags=["users"])
PROTECTED_ROLES = {UserRole.OWNER, UserRole.ADMIN}
DELEGATED_ROLES = {UserRole.MAINTAINER, UserRole.VIEWER}


def _role_value(role: str | UserRole) -> str:
    """Normalize role values for storage and comparisons."""
    return str(role)


def _can_manage_role(actor: User, role: str) -> bool:
    """Return whether an actor may manage accounts with a target role."""
    if actor.role == UserRole.OWNER:
        return True
    if actor.role == UserRole.ADMIN:
        return role in {role.value for role in DELEGATED_ROLES}
    return False


async def _user_or_404(session: DbSession, user_id: str) -> User:
    """Load a user or return a stable not-found response."""
    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    return user


async def _active_owner_count(session: DbSession) -> int:
    """Return the number of active owner accounts."""
    count = await session.scalar(
        select(func.count())
        .select_from(User)
        .where(User.role == UserRole.OWNER, User.is_active.is_(True))
    )
    return int(count or 0)


async def _ensure_owner_not_removed(session: DbSession, user: User) -> None:
    """Reject disabling the last active owner account."""
    if (
        user.role == UserRole.OWNER
        and user.is_active
        and await _active_owner_count(session) <= 1
    ):
        raise HTTPException(status_code=409, detail="Cannot disable the last active owner")


@router.get("", response_model=list[UserResponse])
async def list_users(user: CurrentUser, session: DbSession) -> list[User]:
    """List panel users in deterministic order."""
    require_global_role(user, UserRole.OWNER, UserRole.ADMIN)
    users = await session.scalars(select(User).order_by(User.created_at.asc()))
    return list(users)


@router.post("", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def create_user(
    data: UserCreate,
    request: Request,
    user: CurrentUser,
    session: DbSession,
) -> User:
    """Create a panel user without exposing the password."""
    require_global_role(user, UserRole.OWNER, UserRole.ADMIN)
    target_role = _role_value(data.role)
    if not _can_manage_role(user, target_role):
        raise HTTPException(status_code=403, detail="Cannot manage this role")
    created = User(
        username=data.username,
        password_hash=hash_password(data.password),
        display_name=data.display_name,
        role=target_role,
        is_active=True,
    )
    session.add(created)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status_code=409, detail="Username already exists") from exc
    await write_audit_log(
        session,
        action="user.create",
        actor_id=user.id,
        resource_type="user",
        resource_id=created.id,
        ip_address=request.client.host if request.client else None,
        details={"username": created.username, "role": created.role},
    )
    await session.commit()
    await session.refresh(created)
    return created


@router.patch("/{user_id}", response_model=UserResponse)
async def patch_user(
    user_id: str,
    data: UserUpdate,
    request: Request,
    user: CurrentUser,
    session: DbSession,
) -> User:
    """Update a user account without exposing credentials."""
    require_global_role(user, UserRole.OWNER, UserRole.ADMIN)
    target = await _user_or_404(session, user_id)
    if not _can_manage_role(user, target.role):
        raise HTTPException(status_code=403, detail="Cannot manage this user")
    next_role = (
        _role_value(data.role)
        if "role" in data.model_fields_set and data.role is not None
        else target.role
    )
    if not _can_manage_role(user, next_role):
        raise HTTPException(status_code=403, detail="Cannot assign this role")
    if target.role == UserRole.OWNER and next_role != UserRole.OWNER:
        await _ensure_owner_not_removed(session, target)
    changed: list[str] = []
    if "display_name" in data.model_fields_set:
        target.display_name = data.display_name
        changed.append("display_name")
    if "role" in data.model_fields_set and next_role != target.role:
        target.role = next_role
        changed.append("role")
    if "password" in data.model_fields_set and data.password is not None:
        target.password_hash = hash_password(data.password)
        changed.append("password")
    await write_audit_log(
        session,
        action="user.update",
        actor_id=user.id,
        resource_type="user",
        resource_id=target.id,
        ip_address=request.client.host if request.client else None,
        details={"fields": changed, "role": target.role},
    )
    await session.commit()
    await session.refresh(target)
    return target


@router.post("/{user_id}/disable", response_model=MessageResponse)
async def disable_user(
    user_id: str,
    request: Request,
    user: CurrentUser,
    session: DbSession,
) -> MessageResponse:
    """Disable a user account while preserving audit history."""
    require_global_role(user, UserRole.OWNER, UserRole.ADMIN)
    target = await _user_or_404(session, user_id)
    if not _can_manage_role(user, target.role):
        raise HTTPException(status_code=403, detail="Cannot manage this user")
    await _ensure_owner_not_removed(session, target)
    target.is_active = False
    target.disabled_at = datetime.now(UTC)
    await write_audit_log(
        session,
        action="user.disable",
        actor_id=user.id,
        resource_type="user",
        resource_id=target.id,
        ip_address=request.client.host if request.client else None,
        details={"username": target.username, "role": target.role},
    )
    await session.commit()
    return MessageResponse(message="User disabled")


@router.post("/{user_id}/enable", response_model=MessageResponse)
async def enable_user(
    user_id: str,
    request: Request,
    user: CurrentUser,
    session: DbSession,
) -> MessageResponse:
    """Enable a previously disabled user account."""
    require_global_role(user, UserRole.OWNER, UserRole.ADMIN)
    target = await _user_or_404(session, user_id)
    if not _can_manage_role(user, target.role):
        raise HTTPException(status_code=403, detail="Cannot manage this user")
    target.is_active = True
    target.disabled_at = None
    await write_audit_log(
        session,
        action="user.enable",
        actor_id=user.id,
        resource_type="user",
        resource_id=target.id,
        ip_address=request.client.host if request.client else None,
        details={"username": target.username, "role": target.role},
    )
    await session.commit()
    return MessageResponse(message="User enabled")


async def load_project_members(session: DbSession, project_id: str) -> list[ProjectMembership]:
    """Load project memberships with attached user records."""
    result = await session.scalars(
        select(ProjectMembership)
        .options(selectinload(ProjectMembership.user))
        .where(ProjectMembership.project_id == project_id)
        .order_by(ProjectMembership.created_at.asc())
    )
    return list(result)
