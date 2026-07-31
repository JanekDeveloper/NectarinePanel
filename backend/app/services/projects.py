"""Project business operations."""

import re
import unicodedata

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import Project, ProjectRuntime, ProjectSource
from app.schemas.project import ProjectCreate, ProjectUpdate


def slugify(value: str) -> str:
    """Create a stable lowercase URL slug."""
    normalized = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", normalized.lower()).strip("-")
    if not slug:
        raise ValueError("Project name must contain alphanumeric characters")
    return slug[:120]


async def unique_slug(session: AsyncSession, name: str) -> str:
    """Generate a unique project slug."""
    base = slugify(name)
    candidate = base
    suffix = 2
    while await session.scalar(select(Project.id).where(Project.slug == candidate)):
        candidate = f"{base[:115]}-{suffix}"
        suffix += 1
    return candidate


async def create_project(session: AsyncSession, data: ProjectCreate) -> Project:
    """Create a validated project entity."""
    payload = data.model_dump(mode="json")
    payload["repository_url"] = (
        str(data.repository_url) if data.repository_url is not None else None
    )
    payload["healthcheck_url"] = (
        str(data.healthcheck_url) if data.healthcheck_url is not None else None
    )
    project = Project(slug=await unique_slug(session, data.name), **payload)
    session.add(project)
    await session.flush()
    session.add(
        ProjectRuntime(
            project_id=project.id,
            runtime_type=project.runtime_type,
            configuration=project.runtime_config,
        )
    )
    session.add(
        ProjectSource(
            project_id=project.id,
            source_type="git" if project.repository_url else "files",
            repository_url=project.repository_url,
            branch=project.branch,
        )
    )
    return project


def update_project(project: Project, data: ProjectUpdate) -> None:
    """Apply safe mutable project fields."""
    updates = data.model_dump(exclude_unset=True, mode="json")
    for field, value in updates.items():
        setattr(project, field, value)
