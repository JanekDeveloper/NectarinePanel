"""Project backup, restore, and one-time download endpoints."""

import secrets
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import anyio
from croniter import croniter
from fastapi import APIRouter, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import select, update

from app.api.dependencies import AppSettings, CurrentUser, DbSession
from app.core.security import token_digest
from app.models.entities import Backup, BackupPolicy, Job, OneTimeDownloadLink, Project
from app.schemas.backup import (
    BackupCreateRequest,
    BackupDeleteRequest,
    BackupJobResponse,
    BackupPolicyInput,
    BackupPolicyResponse,
    BackupResponse,
    DownloadLinkResponse,
    FullBackupCreateRequest,
    RestoreRequest,
)
from app.services.audit import write_audit_log
from app.services.paths import runtime_root
from app.services.queue import enqueue

router = APIRouter(tags=["backups"])


def _backup_path_or_409(backup: Backup, settings: AppSettings) -> Path:
    """Resolve a stored backup path and enforce backup-root containment."""
    if not backup.path:
        raise HTTPException(status_code=409, detail="Backup artifact is not ready")
    root = (settings.storage_root / "backups").resolve()
    candidate = Path(backup.path).resolve()
    if candidate != root and root not in candidate.parents:
        raise HTTPException(status_code=409, detail="Stored backup path is invalid")
    if not candidate.is_file():
        raise HTTPException(status_code=410, detail="Backup artifact is missing")
    return candidate


@router.get("/backups", response_model=list[BackupResponse])
async def list_backups(user: CurrentUser, session: DbSession) -> list[Backup]:
    """List all project and database backups."""
    del user
    result = await session.scalars(select(Backup).order_by(Backup.created_at.desc()))
    return list(result)


@router.post(
    "/backups/full",
    response_model=BackupJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_full_backup(
    data: FullBackupCreateRequest,
    user: CurrentUser,
    session: DbSession,
    settings: AppSettings,
) -> BackupJobResponse:
    """Queue a full-panel backup for configs, panel DB and storage state."""
    if data.encryption_enabled and not settings.field_encryption_key:
        raise HTTPException(status_code=409, detail="Backup encryption key is not configured")
    backup = Backup(
        backup_type="full",
        status="queued",
    )
    session.add(backup)
    await session.flush()
    job = Job(
        kind="backup.create_full",
        payload={"backup_id": backup.id},
    )
    session.add(job)
    await write_audit_log(
        session,
        action="backup.create_full",
        actor_id=user.id,
        resource_type="backup",
        resource_id=backup.id,
        details={
            "include_items": data.include_items,
            "encrypted": data.encryption_enabled,
        },
    )
    await session.commit()
    enqueue(
        "backups.create_full",
        task_id=job.id,
        kwargs={
            "backup_id": backup.id,
            "backup_root": str(settings.storage_root / "backups"),
            "include_items": data.include_items,
            "encryption_enabled": data.encryption_enabled,
        },
    )
    return BackupJobResponse(
        backup=BackupResponse.model_validate(backup),
        job_id=job.id,
    )


@router.get(
    "/projects/{project_id}/backups",
    response_model=list[BackupResponse],
)
async def list_project_backups(
    project_id: str, user: CurrentUser, session: DbSession
) -> list[Backup]:
    """List backups belonging to one project."""
    del user
    result = await session.scalars(
        select(Backup).where(Backup.project_id == project_id).order_by(Backup.created_at.desc())
    )
    return list(result)


@router.post(
    "/projects/{project_id}/backups",
    response_model=BackupJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_project_backup(
    project_id: str,
    user: CurrentUser,
    session: DbSession,
    settings: AppSettings,
    data: BackupCreateRequest | None = None,
) -> BackupJobResponse:
    """Queue a compressed project backup with a checksum manifest."""
    project = await session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    source = runtime_root(settings.storage_root, project)
    if not source.is_dir():
        raise HTTPException(status_code=409, detail="Project files do not exist yet")
    selection = data or BackupCreateRequest()
    if selection.encryption_enabled and not settings.field_encryption_key:
        raise HTTPException(status_code=409, detail="Backup encryption key is not configured")
    backup = Backup(
        project_id=project.id,
        backup_type="project",
        status="queued",
    )
    session.add(backup)
    await session.flush()
    job = Job(
        kind="backup.create_project",
        payload={"backup_id": backup.id, "project_id": project.id},
    )
    session.add(job)
    await write_audit_log(
        session,
        action="backup.create",
        actor_id=user.id,
        resource_type="backup",
        resource_id=backup.id,
        details={"project_id": project.id},
    )
    await session.commit()
    enqueue(
        "backups.create_project",
        task_id=job.id,
        kwargs={
            "backup_id": backup.id,
            "source": str(source),
            "backup_root": str(settings.storage_root / "backups"),
            "project_id": project.id,
            "include_items": selection.include_items,
            "encryption_enabled": selection.encryption_enabled,
        },
    )
    return BackupJobResponse(
        backup=BackupResponse.model_validate(backup),
        job_id=job.id,
    )


@router.get(
    "/projects/{project_id}/backup-policy",
    response_model=BackupPolicyResponse | None,
)
async def get_backup_policy(
    project_id: str,
    user: CurrentUser,
    session: DbSession,
) -> BackupPolicy | None:
    """Return the scheduled backup policy for one project."""
    del user
    if await session.get(Project, project_id) is None:
        raise HTTPException(status_code=404, detail="Project not found")
    policy: BackupPolicy | None = await session.scalar(
        select(BackupPolicy).where(BackupPolicy.project_id == project_id)
    )
    return policy


@router.put(
    "/projects/{project_id}/backup-policy",
    response_model=BackupPolicyResponse,
)
async def put_backup_policy(
    project_id: str,
    data: BackupPolicyInput,
    user: CurrentUser,
    session: DbSession,
    settings: AppSettings,
) -> BackupPolicy:
    """Create or update a validated scheduled backup policy."""
    if await session.get(Project, project_id) is None:
        raise HTTPException(status_code=404, detail="Project not found")
    if not croniter.is_valid(data.schedule):
        raise HTTPException(status_code=422, detail="Invalid backup schedule")
    if data.encryption_enabled and not settings.field_encryption_key:
        raise HTTPException(status_code=409, detail="Backup encryption key is not configured")
    policy = await session.scalar(
        select(BackupPolicy).where(BackupPolicy.project_id == project_id)
    )
    if policy is None:
        policy = BackupPolicy(project_id=project_id)
        session.add(policy)
    policy.enabled = data.enabled
    policy.schedule = data.schedule
    policy.retention_count = data.retention_count
    policy.include_items = data.include_items
    policy.include_databases = data.include_databases
    policy.encryption_enabled = data.encryption_enabled
    await write_audit_log(
        session,
        action="backup.policy_change",
        actor_id=user.id,
        resource_type="project",
        resource_id=project_id,
        details={
            "enabled": data.enabled,
            "schedule": data.schedule,
            "retention_count": data.retention_count,
            "include_databases": data.include_databases,
            "encrypted": data.encryption_enabled,
        },
    )
    await session.commit()
    await session.refresh(policy)
    return policy


@router.post(
    "/projects/{project_id}/backups/import",
    response_model=BackupJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def import_project_backup(
    project_id: str,
    file: UploadFile,
    user: CurrentUser,
    session: DbSession,
    settings: AppSettings,
    confirm_project_name: str = Form(),
) -> BackupJobResponse:
    """Upload a project backup and queue full archive validation."""
    project = await session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    if confirm_project_name != project.name:
        raise HTTPException(status_code=422, detail="Project name confirmation mismatch")
    filename = (file.filename or "").lower()
    if not filename.endswith((".tar.gz", ".tar.gz.enc")):
        raise HTTPException(status_code=415, detail="Backup must be .tar.gz or .tar.gz.enc")
    backup = Backup(project_id=project.id, backup_type="project", status="queued")
    session.add(backup)
    await session.flush()
    job = Job(
        kind="backup.import",
        payload={"backup_id": backup.id, "project_id": project.id},
    )
    session.add(job)
    incoming = settings.storage_root / "backups" / "incoming"
    incoming.mkdir(parents=True, exist_ok=True)
    suffix = ".tar.gz.enc" if filename.endswith(".enc") else ".tar.gz"
    target = incoming / f"{job.id}{suffix}"
    total = 0
    try:
        async with await anyio.open_file(target, "xb") as handle:
            while chunk := await file.read(1024 * 1024):
                total += len(chunk)
                if total > settings.max_upload_bytes:
                    raise HTTPException(status_code=413, detail="Backup exceeds size limit")
                await handle.write(chunk)
    except Exception:
        target.unlink(missing_ok=True)
        await session.rollback()
        raise
    finally:
        await file.close()
    await write_audit_log(
        session,
        action="backup.import",
        actor_id=user.id,
        resource_type="backup",
        resource_id=backup.id,
        details={"project_id": project.id, "size_bytes": total},
    )
    await session.commit()
    enqueue(
        "backups.import",
        task_id=job.id,
        kwargs={
            "backup_id": backup.id,
            "project_id": project.id,
            "incoming_path": str(target),
            "backup_root": str(settings.storage_root / "backups"),
        },
    )
    return BackupJobResponse(
        backup=BackupResponse.model_validate(backup),
        job_id=job.id,
    )


@router.post(
    "/backups/{backup_id}/restore",
    response_model=BackupJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def restore_project_backup(
    backup_id: str,
    data: RestoreRequest,
    user: CurrentUser,
    session: DbSession,
    settings: AppSettings,
) -> BackupJobResponse:
    """Queue an atomic restore after exact project-name confirmation."""
    backup = await session.get(Backup, backup_id)
    if backup is None or not backup.project_id:
        raise HTTPException(status_code=404, detail="Project backup not found")
    project = await session.get(Project, backup.project_id)
    if project is None:
        raise HTTPException(status_code=409, detail="Backup project no longer exists")
    if data.confirm_project_name != project.name:
        raise HTTPException(status_code=422, detail="Project name confirmation mismatch")
    archive = _backup_path_or_409(backup, settings)
    job = Job(
        kind="backup.restore_project",
        payload={"backup_id": backup.id, "project_id": project.id},
    )
    session.add(job)
    backup.status = "restoring"
    await write_audit_log(
        session,
        action="backup.restore",
        actor_id=user.id,
        resource_type="backup",
        resource_id=backup.id,
        details={"project_id": project.id},
    )
    await session.commit()
    enqueue(
        "backups.restore_project",
        task_id=job.id,
        kwargs={
            "backup_id": backup.id,
            "project_id": project.id,
            "archive_path": str(archive),
            "destination": str(runtime_root(settings.storage_root, project)),
        },
    )
    return BackupJobResponse(
        backup=BackupResponse.model_validate(backup),
        job_id=job.id,
    )


@router.delete("/backups/{backup_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_backup(
    backup_id: str,
    data: BackupDeleteRequest,
    user: CurrentUser,
    session: DbSession,
    settings: AppSettings,
) -> None:
    """Delete a backup artifact after exact identifier confirmation."""
    backup = await session.get(Backup, backup_id)
    if backup is None:
        raise HTTPException(status_code=404, detail="Backup not found")
    if data.confirm_backup_id != backup.id:
        raise HTTPException(status_code=422, detail="Backup ID confirmation mismatch")
    if backup.path:
        root = (settings.storage_root / "backups").resolve()
        path = Path(backup.path).resolve()
        if root not in path.parents:
            raise HTTPException(status_code=409, detail="Stored backup path is invalid")
        path.unlink(missing_ok=True)
        path.with_suffix(path.suffix + ".manifest.json").unlink(missing_ok=True)
    await write_audit_log(
        session,
        action="backup.delete",
        actor_id=user.id,
        resource_type="backup",
        resource_id=backup.id,
    )
    await session.delete(backup)
    await session.commit()


@router.post(
    "/backups/{backup_id}/download-link",
    response_model=DownloadLinkResponse,
)
async def create_download_link(
    backup_id: str,
    request: Request,
    user: CurrentUser,
    session: DbSession,
    settings: AppSettings,
) -> DownloadLinkResponse:
    """Create a short-lived one-time URL for a ready backup."""
    backup = await session.get(Backup, backup_id)
    if backup is None:
        raise HTTPException(status_code=404, detail="Backup not found")
    path = _backup_path_or_409(backup, settings)
    token = secrets.token_urlsafe(32)
    expires_at = datetime.now(UTC) + timedelta(minutes=10)
    link = OneTimeDownloadLink(
        token_digest=token_digest(token),
        file_path=str(path),
        expires_at=expires_at,
    )
    session.add(link)
    await write_audit_log(
        session,
        action="backup.download_link",
        actor_id=user.id,
        resource_type="backup",
        resource_id=backup.id,
    )
    await session.commit()
    base = str(request.base_url).rstrip("/")
    return DownloadLinkResponse(
        url=f"{base}/api/v1/downloads/{token}",
        expires_at=expires_at,
    )


@router.get("/downloads/{token}", response_class=FileResponse)
async def consume_download_link(
    token: str,
    session: DbSession,
    settings: AppSettings,
) -> FileResponse:
    """Atomically consume a valid one-time artifact link."""
    digest = token_digest(token)
    now = datetime.now(UTC)
    link = await session.scalar(
        select(OneTimeDownloadLink).where(OneTimeDownloadLink.token_digest == digest)
    )
    if link is None:
        raise HTTPException(status_code=404, detail="Download link not found")
    expires_at = (
        link.expires_at
        if link.expires_at.tzinfo is not None
        else link.expires_at.replace(tzinfo=UTC)
    )
    if link.consumed_at is not None or expires_at <= now:
        raise HTTPException(status_code=410, detail="Download link expired or consumed")
    result = await session.execute(
        update(OneTimeDownloadLink)
        .where(
            OneTimeDownloadLink.id == link.id,
            OneTimeDownloadLink.consumed_at.is_(None),
        )
        .values(consumed_at=now)
    )
    if cast(Any, result).rowcount != 1:
        await session.rollback()
        raise HTTPException(status_code=410, detail="Download link already consumed")
    await session.commit()
    root = (settings.storage_root / "backups").resolve()
    path = Path(link.file_path).resolve()
    if root not in path.parents or not path.is_file():
        raise HTTPException(status_code=410, detail="Artifact is unavailable")
    return FileResponse(
        path,
        filename=path.name,
        media_type="application/gzip",
    )
