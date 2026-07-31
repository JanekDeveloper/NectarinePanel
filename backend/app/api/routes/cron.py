"""Project cron schedule CRUD."""

import hashlib

from croniter import croniter
from fastapi import APIRouter, HTTPException, Response, status
from sqlalchemy import select

from app.api.dependencies import CurrentUser, DbSession
from app.models.entities import CronJob, Project
from app.schemas.cron import CronJobCreate, CronJobResponse
from app.services.audit import write_audit_log

router = APIRouter(prefix="/projects/{project_id}/cron", tags=["cron"])
CRON_RUNTIME_TYPES = {"docker", "docker_compose", "systemd", "pm2", "minecraft_forge"}


@router.get("", response_model=list[CronJobResponse])
async def list_cron_jobs(
    project_id: str, user: CurrentUser, session: DbSession
) -> list[CronJob]:
    """List scheduled commands for one project."""
    del user
    result = await session.scalars(
        select(CronJob).where(CronJob.project_id == project_id).order_by(CronJob.name)
    )
    return list(result)


@router.post(
    "",
    response_model=CronJobResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_cron_job(
    project_id: str,
    data: CronJobCreate,
    user: CurrentUser,
    session: DbSession,
) -> CronJob:
    """Create a validated project cron schedule."""
    project = await session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    if project.runtime_type not in CRON_RUNTIME_TYPES:
        raise HTTPException(
            status_code=409,
            detail="Cron requires Docker, Compose, systemd, PM2, or Minecraft runtime",
        )
    if not croniter.is_valid(data.expression):
        raise HTTPException(status_code=422, detail="Invalid cron expression")
    job = CronJob(project_id=project.id, **data.model_dump())
    session.add(job)
    await session.flush()
    await write_audit_log(
        session,
        action="cron.create",
        actor_id=user.id,
        resource_type="cron_job",
        resource_id=job.id,
        details={
            "name": job.name,
            "expression": job.expression,
            "command_sha256": hashlib.sha256(job.command.encode()).hexdigest(),
        },
    )
    await session.commit()
    await session.refresh(job)
    return job


@router.delete("/{cron_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_cron_job(
    project_id: str,
    cron_id: str,
    user: CurrentUser,
    session: DbSession,
) -> Response:
    """Delete one project schedule."""
    job = await session.get(CronJob, cron_id)
    if job is None or job.project_id != project_id:
        raise HTTPException(status_code=404, detail="Cron job not found")
    await write_audit_log(
        session,
        action="cron.delete",
        actor_id=user.id,
        resource_type="cron_job",
        resource_id=job.id,
        details={"name": job.name},
    )
    await session.delete(job)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
