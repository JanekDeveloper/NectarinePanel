"""Authenticated internal API consumed by the owner Telegram bot."""

import secrets
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Literal

import anyio
from fastapi import APIRouter, Depends, Form, Header, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import select

from app.api.dependencies import AppSettings, DbSession
from app.core.security import token_digest
from app.models.entities import (
    MINECRAFT_TYPES,
    Backup,
    DatabaseInstance,
    Job,
    LoginChallenge,
    OneTimeDownloadLink,
    Project,
    TelegramAccount,
    User,
)
from app.services.agent import AgentClient
from app.services.audit import write_audit_log
from app.services.minecraft import control_minecraft_runtime
from app.services.minecraft_operations import minecraft_action, reserve_minecraft_job
from app.services.paths import runtime_root
from app.services.queue import enqueue
from app.services.system_settings import telegram_runtime_settings

router = APIRouter(prefix="/telegram", tags=["telegram-internal"])


async def require_telegram_service(
    settings: AppSettings,
    token: Annotated[str | None, Header(alias="X-Telegram-Service-Token")] = None,
) -> None:
    """Authenticate the bot with a constant-time internal service token."""
    if not token or not secrets.compare_digest(token, settings.telegram_api_token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Telegram service credential",
        )


TelegramService = Annotated[None, Depends(require_telegram_service)]


class TelegramProjectAction(BaseModel):
    """Owner-approved project action from Telegram."""

    action: Literal["start", "stop", "restart", "backup"]


class TelegramBackupRestore(BaseModel):
    """Owner-approved backup restore request from Telegram."""

    telegram_user_id: int
    username: str | None = None


class TelegramIdentity(BaseModel):
    """Verified Telegram identity supplied by the allowlisted bot."""

    telegram_user_id: int
    username: str | None = None


class TelegramRuntimeConfig(BaseModel):
    """Secret runtime configuration returned only to the bot service."""

    bot_token: str
    owner_id: int | None
    max_file_bytes: int


async def _configured_telegram_owner(
    session: DbSession,
    settings: AppSettings,
) -> int | None:
    """Return the configured Telegram owner without exposing secrets."""
    return (await telegram_runtime_settings(session, settings)).owner_id


async def _require_owner_identity(
    identity: TelegramIdentity,
    session: DbSession,
    settings: AppSettings,
) -> None:
    """Reject internal requests that do not identify the configured owner."""
    if identity.telegram_user_id != await _configured_telegram_owner(session, settings):
        raise HTTPException(status_code=403, detail="Telegram owner mismatch")


async def _save_upload(
    file: UploadFile,
    target: Path,
    max_bytes: int,
) -> int:
    """Persist a bounded Telegram upload without trusting the filename."""
    target.parent.mkdir(parents=True, exist_ok=True)
    total = 0
    try:
        async with await anyio.open_file(target, "xb") as handle:
            while chunk := await file.read(1024 * 1024):
                total += len(chunk)
                if total > max_bytes:
                    raise HTTPException(status_code=413, detail="Upload exceeds size limit")
                await handle.write(chunk)
    except Exception:
        target.unlink(missing_ok=True)
        raise
    finally:
        await file.close()
    return total


@router.get("/runtime-config", response_model=TelegramRuntimeConfig)
async def telegram_runtime_config(
    service: TelegramService,
    session: DbSession,
    settings: AppSettings,
) -> TelegramRuntimeConfig:
    """Return effective bot settings over the authenticated internal channel."""
    del service
    runtime = await telegram_runtime_settings(session, settings)
    return TelegramRuntimeConfig(
        bot_token=runtime.bot_token,
        owner_id=runtime.owner_id,
        max_file_bytes=settings.telegram_max_file_bytes,
    )


@router.post("/bind")
async def bind_telegram_owner(
    identity: TelegramIdentity,
    service: TelegramService,
    session: DbSession,
    settings: AppSettings,
) -> dict[str, bool]:
    """Bind the allowlisted Telegram identity to the panel owner."""
    del service
    await _require_owner_identity(identity, session, settings)
    user = await session.scalar(select(User).order_by(User.created_at).limit(1))
    if user is None:
        raise HTTPException(status_code=409, detail="Panel owner is not initialized")
    account = await session.scalar(
        select(TelegramAccount).where(TelegramAccount.user_id == user.id)
    )
    if account is None:
        account = TelegramAccount(
            user_id=user.id,
            telegram_user_id=identity.telegram_user_id,
        )
        session.add(account)
    account.telegram_user_id = identity.telegram_user_id
    account.username = identity.username
    account.is_verified = True
    await write_audit_log(
        session,
        action="telegram.bind",
        actor_id=user.id,
        resource_type="telegram_account",
        resource_id=account.id,
    )
    await session.commit()
    return {"bound": True}


@router.post("/2fa/{challenge_token}/approve")
async def approve_two_factor(
    challenge_token: str,
    identity: TelegramIdentity,
    service: TelegramService,
    session: DbSession,
    settings: AppSettings,
) -> dict[str, bool]:
    """Approve one pending login challenge from the allowlisted owner."""
    del service
    await _require_owner_identity(identity, session, settings)
    challenge = await session.scalar(
        select(LoginChallenge).where(
            LoginChallenge.token_digest == token_digest(challenge_token)
        )
    )
    if challenge is None:
        raise HTTPException(status_code=404, detail="Two-factor challenge not found")
    now = datetime.now(UTC)
    expires_at = (
        challenge.expires_at
        if challenge.expires_at.tzinfo is not None
        else challenge.expires_at.replace(tzinfo=UTC)
    )
    if challenge.consumed_at is not None or expires_at <= now:
        raise HTTPException(status_code=410, detail="Two-factor challenge expired")
    challenge.approved_at = now
    await write_audit_log(
        session,
        action="auth.2fa_approved",
        actor_id=challenge.user_id,
        resource_type="login_challenge",
        resource_id=challenge.id,
    )
    await session.commit()
    return {"approved": True}


@router.get("/projects")
async def telegram_projects(
    service: TelegramService,
    session: DbSession,
) -> list[dict[str, str]]:
    """List projects for owner bot controls."""
    del service
    projects = await session.scalars(select(Project).order_by(Project.name))
    return [
        {"id": project.id, "name": project.name, "status": project.status}
        for project in projects
    ]


@router.post("/projects/{project_id}/actions")
async def telegram_project_action(
    project_id: str,
    data: TelegramProjectAction,
    service: TelegramService,
    session: DbSession,
    settings: AppSettings,
) -> dict[str, str]:
    """Execute or queue a confirmed project lifecycle action."""
    del service
    project = await session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    if data.action == "backup":
        source = runtime_root(settings.storage_root, project)
        if not source.is_dir():
            raise HTTPException(status_code=409, detail="Project files do not exist")
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
        await reserve_minecraft_job(session, project, job)
        await session.commit()
        enqueue(
            "backups.create_project",
            task_id=job.id,
            kwargs={
                "backup_id": backup.id,
                "source": str(source),
                "backup_root": str(settings.storage_root / "backups"),
                "project_id": project.id,
            },
        )
    else:
        if project.runtime_type not in {"docker", *MINECRAFT_TYPES}:
            raise HTTPException(status_code=409, detail="Unsupported runtime action")
        if project.runtime_type in MINECRAFT_TYPES:
            async with minecraft_action(session, project, data.action):
                await control_minecraft_runtime(project, data.action, session, settings)
        else:
            await AgentClient(settings).execute(
                "manage_container",
                {"container": f"vps-project-{project.id}", "action": data.action},
                request_timeout=150,
            )
        project.status = "stopped" if data.action == "stop" else "running"
        job = Job(
            kind=f"telegram.project_{data.action}",
            status="success",
            progress=100,
            payload={"project_id": project.id},
            result={"action": data.action},
        )
        session.add(job)
    await write_audit_log(
        session,
        action=f"telegram.project_{data.action}",
        actor_id=None,
        resource_type="project",
        resource_id=project.id,
    )
    await session.commit()
    return {"job_id": job.id}


@router.get("/databases")
async def telegram_databases(
    service: TelegramService, session: DbSession
) -> list[dict[str, str]]:
    """List managed databases for export selection."""
    del service
    databases = await session.scalars(select(DatabaseInstance).order_by(DatabaseInstance.name))
    return [
        {
            "id": database.id,
            "name": database.name,
            "engine": database.engine,
            "status": database.status,
        }
        for database in databases
    ]


@router.post("/databases/{database_id}/export")
async def telegram_database_export(
    database_id: str,
    service: TelegramService,
    session: DbSession,
) -> dict[str, str]:
    """Queue a database dump requested by the owner bot."""
    del service
    database = await session.get(DatabaseInstance, database_id)
    if database is None or database.status != "ready":
        raise HTTPException(status_code=409, detail="Database is not ready")
    backup = Backup(
        database_id=database.id,
        backup_type="database",
        status="queued",
    )
    session.add(backup)
    await session.flush()
    job = Job(
        kind="database.export",
        payload={"database_id": database.id, "backup_id": backup.id},
    )
    session.add(job)
    await write_audit_log(
        session,
        action="telegram.database_export",
        actor_id=None,
        resource_type="database",
        resource_id=database.id,
    )
    await session.commit()
    enqueue(
        "databases.export",
        task_id=job.id,
        kwargs={"database_id": database.id, "backup_id": backup.id},
    )
    return {"job_id": job.id, "backup_id": backup.id}


@router.get("/backups")
async def telegram_backups(
    service: TelegramService, session: DbSession
) -> list[dict[str, str | int | None]]:
    """List ready backups available to the owner bot."""
    del service
    backups = await session.scalars(
        select(Backup)
        .where(Backup.status == "ready")
        .order_by(Backup.created_at.desc())
        .limit(50)
    )
    return [
        {
            "id": backup.id,
            "type": backup.backup_type,
            "size_bytes": backup.size_bytes,
            "project_id": backup.project_id,
            "database_id": backup.database_id,
        }
        for backup in backups
    ]


@router.get("/backups/{backup_id}/artifact", response_class=FileResponse)
async def telegram_backup_artifact(
    backup_id: str,
    service: TelegramService,
    session: DbSession,
    settings: AppSettings,
) -> FileResponse:
    """Stream a ready artifact only to the authenticated bot service."""
    del service
    backup = await session.get(Backup, backup_id)
    if backup is None or backup.status != "ready" or not backup.path:
        raise HTTPException(status_code=404, detail="Backup artifact not found")
    root = (settings.storage_root / "backups").resolve()
    path = Path(backup.path).resolve()
    if root not in path.parents or not path.is_file():
        raise HTTPException(status_code=410, detail="Backup artifact is unavailable")
    return FileResponse(path, filename=path.name)


@router.get("/backups/{backup_id}")
async def telegram_backup_metadata(
    backup_id: str,
    service: TelegramService,
    session: DbSession,
    settings: AppSettings,
) -> dict[str, str | int | None]:
    """Return backup metadata before Telegram decides between file and link."""
    del service
    backup = await session.get(Backup, backup_id)
    if backup is None or backup.status != "ready" or not backup.path:
        raise HTTPException(status_code=404, detail="Backup not found")
    root = (settings.storage_root / "backups").resolve()
    path = Path(backup.path).resolve()
    if root not in path.parents or not path.is_file():
        raise HTTPException(status_code=410, detail="Backup artifact is unavailable")
    return {
        "id": backup.id,
        "type": backup.backup_type,
        "size_bytes": backup.size_bytes or path.stat().st_size,
        "project_id": backup.project_id,
        "database_id": backup.database_id,
        "filename": path.name,
    }


@router.post("/backups/{backup_id}/download-link")
async def telegram_backup_link(
    backup_id: str,
    service: TelegramService,
    session: DbSession,
    settings: AppSettings,
) -> dict[str, str]:
    """Create a one-time public link when Telegram file limits are exceeded."""
    del service
    backup = await session.get(Backup, backup_id)
    if backup is None or backup.status != "ready" or not backup.path:
        raise HTTPException(status_code=404, detail="Backup not found")
    token = secrets.token_urlsafe(32)
    expires = datetime.now(UTC) + timedelta(minutes=10)
    session.add(
        OneTimeDownloadLink(
            token_digest=token_digest(token),
            file_path=backup.path,
            expires_at=expires,
        )
    )
    await write_audit_log(
        session,
        action="telegram.backup_link",
        actor_id=None,
        resource_type="backup",
        resource_id=backup.id,
    )
    await session.commit()
    return {
        "url": f"{settings.public_base_url.rstrip('/')}/api/v1/downloads/{token}",
        "expires_at": expires.isoformat(),
    }


@router.post("/databases/{database_id}/restore")
async def telegram_database_restore(
    database_id: str,
    service: TelegramService,
    session: DbSession,
    settings: AppSettings,
    file: UploadFile,
    telegram_user_id: int = Form(),
    username: str | None = Form(default=None),
) -> dict[str, str]:
    """Upload a database dump from Telegram and queue a destructive restore."""
    del service
    await _require_owner_identity(
        TelegramIdentity(telegram_user_id=telegram_user_id, username=username),
        session,
        settings,
    )
    database = await session.get(DatabaseInstance, database_id)
    if database is None or database.status != "ready":
        raise HTTPException(status_code=409, detail="Database is not ready")
    if database.engine == "sqlite" and database.project_id:
        project = await session.get(Project, database.project_id)
        if project is not None and project.status in {"running", "deploying"}:
            raise HTTPException(
                status_code=409,
                detail="Stop the attached project before restoring SQLite",
            )
    filename = file.filename or ""
    suffix = Path(filename).suffix.lower()
    if database.engine == "postgresql":
        expected_suffixes = {".dump"}
    elif database.engine in {"mysql", "mariadb"}:
        expected_suffixes = {".sql"}
    elif database.engine == "sqlite":
        expected_suffixes = {".db", ".sqlite", ".sqlite3"}
    else:
        raise HTTPException(status_code=409, detail="Database engine cannot be restored")
    if suffix not in expected_suffixes:
        raise HTTPException(status_code=415, detail="Dump format does not match engine")
    job = Job(kind="database.import", payload={"database_id": database.id})
    session.add(job)
    await session.flush()
    target = settings.storage_root / "backups" / "incoming" / f"{job.id}{suffix}"
    total = await _save_upload(file, target, settings.max_upload_bytes)
    await write_audit_log(
        session,
        action="telegram.database_import",
        actor_id=None,
        resource_type="database",
        resource_id=database.id,
        details={"telegram_user_id": telegram_user_id, "size_bytes": total},
    )
    await session.commit()
    enqueue(
        "databases.import",
        task_id=job.id,
        kwargs={"database_id": database.id, "dump_path": str(target)},
    )
    return {"job_id": job.id}


@router.post("/projects/{project_id}/restore")
async def telegram_project_backup_import(
    project_id: str,
    service: TelegramService,
    session: DbSession,
    settings: AppSettings,
    file: UploadFile,
    telegram_user_id: int = Form(),
    username: str | None = Form(default=None),
) -> dict[str, str]:
    """Upload a project backup from Telegram and queue archive validation."""
    del service
    await _require_owner_identity(
        TelegramIdentity(telegram_user_id=telegram_user_id, username=username),
        session,
        settings,
    )
    project = await session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
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
    suffix = ".tar.gz.enc" if filename.endswith(".enc") else ".tar.gz"
    target = settings.storage_root / "backups" / "incoming" / f"{job.id}{suffix}"
    total = await _save_upload(file, target, settings.max_upload_bytes)
    await write_audit_log(
        session,
        action="telegram.backup_import",
        actor_id=None,
        resource_type="backup",
        resource_id=backup.id,
        details={
            "project_id": project.id,
            "telegram_user_id": telegram_user_id,
            "size_bytes": total,
        },
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
    return {"job_id": job.id, "backup_id": backup.id}


@router.post("/backups/{backup_id}/restore")
async def telegram_restore_backup(
    backup_id: str,
    data: TelegramBackupRestore,
    service: TelegramService,
    session: DbSession,
    settings: AppSettings,
) -> dict[str, str]:
    """Queue a project backup restore after owner inline confirmation."""
    del service
    await _require_owner_identity(
        TelegramIdentity(telegram_user_id=data.telegram_user_id, username=data.username),
        session,
        settings,
    )
    backup = await session.get(Backup, backup_id)
    if backup is None or backup.status != "ready" or not backup.project_id:
        raise HTTPException(status_code=404, detail="Project backup not found")
    project = await session.get(Project, backup.project_id)
    if project is None:
        raise HTTPException(status_code=409, detail="Backup project no longer exists")
    if not backup.path:
        raise HTTPException(status_code=409, detail="Backup artifact is not ready")
    root = (settings.storage_root / "backups").resolve()
    archive = Path(backup.path).resolve()
    if root not in archive.parents or not archive.is_file():
        raise HTTPException(status_code=410, detail="Backup artifact is unavailable")
    job = Job(
        kind="backup.restore_project",
        payload={"backup_id": backup.id, "project_id": project.id},
    )
    session.add(job)
    backup.status = "restoring"
    await write_audit_log(
        session,
        action="telegram.backup_restore",
        actor_id=None,
        resource_type="backup",
        resource_id=backup.id,
        details={"project_id": project.id, "telegram_user_id": data.telegram_user_id},
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
    return {"job_id": job.id}
