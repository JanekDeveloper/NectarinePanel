"""Role and project permission helpers."""

from collections.abc import Iterable

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import Project, ProjectMembership, User, UserRole

GLOBAL_PROJECT_ROLES = {UserRole.OWNER, UserRole.ADMIN}
PROJECT_PERMISSIONS: dict[str, set[str]] = {
    "maintainer": {
        "project:read",
        "project:update",
        "deploy:write",
        "runtime:control",
        "logs:read",
        "console:write",
        "files:read",
        "files:write",
        "env:read",
        "env:write",
    },
    "viewer": {
        "project:read",
        "logs:read",
        "files:read",
        "env:read",
    },
}


def has_global_role(user: User, roles: Iterable[str | UserRole]) -> bool:
    """Return whether a user has one of the requested global roles."""
    allowed = {str(role) for role in roles}
    return user.role in allowed


def require_global_role(user: User, *roles: str | UserRole) -> None:
    """Reject a request unless the user has one of the requested global roles."""
    if not has_global_role(user, roles):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Insufficient permissions",
        )


async def accessible_project_ids(session: AsyncSession, user: User) -> list[str] | None:
    """Return assigned project IDs or None when the user has global project access."""
    if user.role in {UserRole.OWNER, UserRole.ADMIN}:
        return None
    memberships = await session.scalars(
        select(ProjectMembership.project_id).where(ProjectMembership.user_id == user.id)
    )
    return list(memberships)


async def project_or_404(session: AsyncSession, project_id: str) -> Project:
    """Load a project or return a stable not-found response."""
    project = await session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


async def require_project_permission(
    session: AsyncSession,
    user: User,
    project_id: str,
    permission: str,
) -> Project:
    """Load a project and reject the request if the user lacks access."""
    project = await project_or_404(session, project_id)
    if user.role in {UserRole.OWNER, UserRole.ADMIN}:
        return project
    membership = await session.scalar(
        select(ProjectMembership).where(
            ProjectMembership.project_id == project_id,
            ProjectMembership.user_id == user.id,
        )
    )
    if membership is None or permission not in PROJECT_PERMISSIONS.get(membership.role, set()):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Insufficient project permissions",
        )
    return project
