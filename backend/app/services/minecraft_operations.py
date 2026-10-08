"""Transactional Minecraft operation reservations shared by API and workers."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import MINECRAFT_TYPES, Job, Project, ProjectRuntime


async def assert_minecraft_idle(session: AsyncSession, project: Project) -> None:
    """Lock runtime metadata and reject mutations during reserved operations."""
    if project.runtime_type not in MINECRAFT_TYPES:
        return
    runtime = await session.scalar(
        select(ProjectRuntime).where(ProjectRuntime.project_id == project.id).with_for_update()
    )
    if runtime is None or runtime.active_job_id is not None:
        raise HTTPException(status_code=409, detail="Minecraft operation in progress")


async def reserve_minecraft_job(session: AsyncSession, project: Project, job: Job) -> None:
    """Atomically associate one durable job with an idle Minecraft runtime."""
    if project.runtime_type not in MINECRAFT_TYPES:
        return
    job.payload = {"phase": "queued", **job.payload}
    session.add(job)
    await session.flush()
    result = await session.execute(
        update(ProjectRuntime)
        .where(ProjectRuntime.project_id == project.id, ProjectRuntime.active_job_id.is_(None))
        .values(active_job_id=job.id)
    )
    if result.rowcount != 1:  # type: ignore[attr-defined]
        raise HTTPException(status_code=409, detail="Minecraft operation in progress")


async def release_minecraft_job(session: AsyncSession, project_id: str, job_id: str) -> None:
    """Release only the reservation owned by the completed job."""
    await session.execute(
        update(ProjectRuntime)
        .where(ProjectRuntime.project_id == project_id, ProjectRuntime.active_job_id == job_id)
        .values(active_job_id=None)
    )


@asynccontextmanager
async def minecraft_action(
    session: AsyncSession, project: Project, action: str
) -> AsyncIterator[None]:
    """Reserve synchronous lifecycle actions and persist sanitized outcomes."""
    job = Job(kind=f"minecraft.{action}", payload={"project_id": project.id}, status="started")
    await reserve_minecraft_job(session, project, job)
    await session.commit()
    try:
        yield
    except BaseException:
        await session.rollback()
        job = await session.get(Job, job.id) or job
        job.status = "failure"
        job.error = "Minecraft operation failed"
        raise
    else:
        job.status = "success"
        job.progress = 100
    finally:
        await release_minecraft_job(session, project.id, job.id)
        await session.commit()
