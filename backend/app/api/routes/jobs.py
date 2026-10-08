"""Project deployment and job progress APIs."""

import asyncio
import hashlib
import hmac
import secrets
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import anyio
import jwt
from fastapi import (
    APIRouter,
    Header,
    HTTPException,
    Query,
    Request,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from sqlalchemy import select

from app.api.dependencies import AppSettings, CurrentUser, DbSession
from app.core.config import Settings, get_settings
from app.core.security import SecretCipher, decode_access_token, token_digest
from app.db.session import SessionFactory
from app.models.entities import (
    Backup,
    Deployment,
    DeploymentLog,
    Domain,
    Job,
    Project,
    SslCertificate,
    User,
    Webhook,
)
from app.schemas.jobs import (
    DeploymentLogResponse,
    DeploymentResponse,
    DeployRequest,
    JobResponse,
    WebhookCreateResponse,
)
from app.services.audit import write_audit_log
from app.services.paths import project_root
from app.services.permissions import require_project_permission
from app.services.queue import enqueue, task_status

router = APIRouter(tags=["jobs"])


def _verify_github_signature(raw_body: bytes, secret: str, signature: str | None) -> None:
    """Validate a GitHub sha256 webhook signature."""
    if not signature or not signature.startswith("sha256="):
        raise HTTPException(status_code=401, detail="Missing GitHub webhook signature")
    expected = (
        "sha256="
        + hmac.new(
            secret.encode("utf-8"),
            raw_body,
            hashlib.sha256,
        ).hexdigest()
    )
    if not hmac.compare_digest(expected, signature):
        raise HTTPException(status_code=401, detail="Invalid GitHub webhook signature")


def _runtime_config(project: Project) -> dict[str, object]:
    """Build the worker runtime config from normalized project fields."""
    config: dict[str, object] = dict(project.runtime_config)
    if (
        project.runtime_type == "docker"
        and project.project_type == "bot"
        and "persistent_mounts" not in config
    ):
        config["persistent_mounts"] = [
            {"source": "data", "target": "/app/data", "read_only": False}
        ]
    if project.output_directory:
        config["output_directory"] = project.output_directory
    if project.install_command:
        config["install_command"] = project.install_command
    if project.build_command:
        config["build_command"] = project.build_command
    if project.start_command:
        config["start_command"] = project.start_command
    if project.healthcheck_url:
        config["healthcheck_url"] = project.healthcheck_url
    return config


async def _prepare_git_deploy(
    project: Project,
    session: DbSession,
    settings: Settings,
    *,
    revision: str | None = None,
) -> tuple[Job, dict[str, Any]]:
    """Create deployment records and return queue kwargs."""
    if not project.repository_url:
        raise HTTPException(status_code=409, detail="Project has no Git repository")
    release_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    root = project_root(settings.storage_root, project.id)
    root.mkdir(parents=True, exist_ok=True)
    job = Job(
        kind="project.git_deploy",
        payload={"project_id": project.id, "release_id": release_id},
    )
    session.add(job)
    await session.flush()
    deployment = Deployment(
        project_id=project.id,
        status="queued",
        source_revision=revision or project.branch,
        started_at=datetime.now(UTC),
    )
    session.add(deployment)
    project.status = "deploying"
    return job, {
        "project_id": project.id,
        "repository_url": project.repository_url,
        "branch": revision or project.branch,
        "project_root": str(root),
        "release_id": release_id,
        "runtime_type": project.runtime_type,
        "runtime_config": _runtime_config(project),
    }


@router.post("/projects/{project_id}/deploy", response_model=JobResponse)
async def deploy_project(
    project_id: str,
    data: DeployRequest,
    user: CurrentUser,
    session: DbSession,
    settings: AppSettings,
) -> Job:
    """Queue a Git deployment into an immutable release directory."""
    project = await require_project_permission(session, user, project_id, "deploy:write")
    job, kwargs = await _prepare_git_deploy(project, session, settings, revision=data.revision)
    await write_audit_log(
        session,
        action="project.deploy",
        actor_id=user.id,
        resource_type="project",
        resource_id=project.id,
        details={"job_id": job.id, "branch": data.revision or project.branch},
    )
    await session.commit()
    enqueue("projects.git_deploy", task_id=job.id, kwargs=kwargs)
    return job


@router.post(
    "/projects/{project_id}/deploy/archive",
    response_model=JobResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def deploy_project_archive(
    project_id: str,
    file: UploadFile,
    user: CurrentUser,
    session: DbSession,
    settings: AppSettings,
) -> Job:
    """Stream a ZIP upload and queue an immutable archive deployment."""
    project = await require_project_permission(session, user, project_id, "deploy:write")
    filename = file.filename or ""
    if not filename.lower().endswith(".zip"):
        raise HTTPException(status_code=415, detail="Deployment archive must be ZIP")
    release_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    root = project_root(settings.storage_root, project.id)
    incoming_root = root / "incoming"
    incoming_root.mkdir(parents=True, exist_ok=True)
    job = Job(
        kind="project.archive_deploy",
        payload={"project_id": project.id, "release_id": release_id},
    )
    session.add(job)
    await session.flush()
    archive = incoming_root / f"{job.id}.zip"
    total = 0
    try:
        async with await anyio.open_file(archive, "xb") as handle:
            while chunk := await file.read(1024 * 1024):
                total += len(chunk)
                if total > settings.max_upload_bytes:
                    raise HTTPException(status_code=413, detail="Archive exceeds size limit")
                await handle.write(chunk)
    except Exception:
        archive.unlink(missing_ok=True)
        await session.rollback()
        raise
    finally:
        await file.close()
    deployment = Deployment(
        project_id=project.id,
        status="queued",
        source_revision=f"upload:{filename[:200]}",
        started_at=datetime.now(UTC),
    )
    session.add(deployment)
    project.status = "deploying"
    await write_audit_log(
        session,
        action="project.deploy_archive",
        actor_id=user.id,
        resource_type="project",
        resource_id=project.id,
        details={"job_id": job.id, "size_bytes": total},
    )
    await session.commit()
    enqueue(
        "projects.archive_deploy",
        task_id=job.id,
        kwargs={
            "project_id": project.id,
            "archive_path": str(archive),
            "project_root": str(root),
            "release_id": release_id,
            "runtime_type": project.runtime_type,
            "runtime_config": _runtime_config(project),
        },
    )
    return job


@router.post(
    "/projects/{project_id}/webhook",
    response_model=WebhookCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_project_webhook(
    project_id: str,
    user: CurrentUser,
    session: DbSession,
    settings: AppSettings,
) -> WebhookCreateResponse:
    """Create a one-time visible GitHub webhook trigger token."""
    project = await require_project_permission(session, user, project_id, "deploy:write")
    if not project.repository_url:
        raise HTTPException(status_code=409, detail="Project has no Git repository")
    token = secrets.token_urlsafe(32)
    hmac_secret = secrets.token_urlsafe(32)
    cipher = SecretCipher(settings.field_encryption_key)
    webhook = Webhook(
        project_id=project.id,
        provider="github",
        secret_digest=token_digest(token),
        encrypted_secret=cipher.encrypt(hmac_secret),
        is_active=True,
    )
    session.add(webhook)
    await session.flush()
    await write_audit_log(
        session,
        action="webhook.create",
        actor_id=user.id,
        resource_type="webhook",
        resource_id=webhook.id,
        details={"project_id": project.id, "provider": "github"},
    )
    await session.commit()
    return WebhookCreateResponse(
        id=webhook.id,
        token=token,
        hmac_secret=hmac_secret,
        url=f"{settings.public_base_url.rstrip('/')}/api/v1/webhooks/github/{token}",
    )


@router.post("/webhooks/github/{token}", response_model=JobResponse)
async def trigger_github_webhook(
    token: str,
    request: Request,
    session: DbSession,
    settings: AppSettings,
    x_hub_signature_256: str | None = Header(default=None, alias="X-Hub-Signature-256"),
) -> Job:
    """Trigger a Git deployment through a stored webhook token digest."""
    if len(token) < 32:
        raise HTTPException(status_code=404, detail="Webhook not found")
    webhook = await session.scalar(
        select(Webhook).where(
            Webhook.provider == "github",
            Webhook.secret_digest == token_digest(token),
            Webhook.is_active.is_(True),
        )
    )
    if webhook is None:
        raise HTTPException(status_code=404, detail="Webhook not found")
    if not webhook.encrypted_secret:
        raise HTTPException(status_code=401, detail="Webhook signature is not configured")
    raw_body = await request.body()
    secret = SecretCipher(settings.field_encryption_key).decrypt(webhook.encrypted_secret)
    _verify_github_signature(raw_body, secret, x_hub_signature_256)
    project = await session.get(Project, webhook.project_id)
    if project is None:
        raise HTTPException(status_code=410, detail="Webhook project is unavailable")
    job, kwargs = await _prepare_git_deploy(project, session, settings)
    await write_audit_log(
        session,
        action="webhook.github",
        actor_id=None,
        resource_type="project",
        resource_id=project.id,
        details={"webhook_id": webhook.id, "job_id": job.id},
    )
    await session.commit()
    enqueue("projects.git_deploy", task_id=job.id, kwargs=kwargs)
    return job


@router.post(
    "/projects/{project_id}/rollback/{deployment_id}",
    response_model=JobResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def rollback_project(
    project_id: str,
    deployment_id: str,
    user: CurrentUser,
    session: DbSession,
    settings: AppSettings,
) -> Job:
    """Queue rollback to a previous successful immutable release."""
    project = await require_project_permission(session, user, project_id, "deploy:write")
    deployment = await session.get(Deployment, deployment_id)
    if (
        deployment is None
        or deployment.project_id != project.id
        or deployment.status != "success"
        or not deployment.release_path
    ):
        raise HTTPException(status_code=409, detail="Deployment is not rollbackable")
    root = project_root(settings.storage_root, project.id)
    release = Path(deployment.release_path).resolve()
    if root.resolve() not in release.parents or not release.is_dir():
        raise HTTPException(status_code=410, detail="Release files are unavailable")
    job = Job(
        kind="project.rollback",
        payload={
            "project_id": project.id,
            "deployment_id": deployment.id,
            "release_id": release.name,
        },
    )
    session.add(job)
    project.status = "deploying"
    await write_audit_log(
        session,
        action="project.rollback",
        actor_id=user.id,
        resource_type="project",
        resource_id=project.id,
        details={"deployment_id": deployment.id},
    )
    await session.commit()
    enqueue(
        "projects.rollback",
        task_id=job.id,
        kwargs={
            "project_id": project.id,
            "project_root": str(root),
            "release_id": release.name,
            "runtime_type": project.runtime_type,
            "runtime_config": _runtime_config(project),
        },
    )
    return job


@router.get("/jobs/{job_id}", response_model=JobResponse)
async def get_job(job_id: str, user: CurrentUser, session: DbSession) -> Job:
    """Return persisted and current queue state for one job."""
    job = await session.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    project_id = job.payload.get("project_id")
    if isinstance(project_id, str):
        await require_project_permission(session, user, project_id, "project:read")
    if job.kind.startswith("minecraft.") or "phase" in job.payload:
        return job
    status = task_status(job.id)
    job.status = status["state"].lower()
    job.progress = int(status.get("progress", job.progress))
    if "result" in status:
        job.result = status["result"]
        if job.kind in {"domain.configure", "domain.renew"} and isinstance(job.result, dict):
            domain_id = job.result.get("domain_id")
            domain = await session.get(Domain, domain_id) if domain_id else None
            if domain is not None:
                domain.ssl_status = str(job.result.get("ssl_status", "disabled"))
                certificate = await session.scalar(
                    select(SslCertificate).where(SslCertificate.domain_id == domain.id)
                )
                if certificate is not None:
                    certificate.status = domain.ssl_status
                    expires_at = job.result.get("expires_at")
                    fingerprint = job.result.get("fingerprint_sha256")
                    if isinstance(expires_at, str):
                        parsed_expiry = datetime.fromisoformat(expires_at)
                        domain.certificate_expires_at = parsed_expiry
                        certificate.expires_at = parsed_expiry
                    if isinstance(fingerprint, str):
                        certificate.fingerprint_sha256 = fingerprint
                    if domain.ssl_status == "active" and certificate.issued_at is None:
                        certificate.issued_at = datetime.now(UTC)
        if job.kind in {
            "backup.create_project",
            "backup.create_full",
            "backup.import",
            "database.export",
        } and isinstance(job.result, dict):
            backup_id = job.result.get("backup_id")
            backup = await session.get(Backup, backup_id) if backup_id else None
            if backup is not None:
                backup.status = "ready"
                backup.path = str(job.result.get("path"))
                backup.size_bytes = int(job.result.get("size_bytes", 0))
                backup.checksum = str(job.result.get("checksum"))
                backup.manifest = job.result
        if job.kind == "backup.restore_project" and isinstance(job.result, dict):
            backup_id = job.result.get("backup_id")
            backup = await session.get(Backup, backup_id) if backup_id else None
            if backup is not None:
                backup.status = "ready"
        if job.kind in {
            "project.git_deploy",
            "project.archive_deploy",
            "project.rollback",
        } and isinstance(job.result, dict):
            project_id = job.result.get("project_id")
            project = await session.get(Project, project_id) if project_id else None
            if project is not None:
                project.status = "running"
            deployment = await session.scalar(
                select(Deployment)
                .where(Deployment.project_id == project_id)
                .order_by(Deployment.created_at.desc())
            )
            if deployment is not None:
                deployment.status = "success"
                deployment.release_path = str(job.result.get("release_path"))
                deployment.log = str(job.result.get("log", ""))[-262144:]
                deployment.finished_at = datetime.now(UTC)
                existing_log = await session.scalar(
                    select(DeploymentLog.id).where(
                        DeploymentLog.deployment_id == deployment.id,
                        DeploymentLog.sequence == 0,
                    )
                )
                if existing_log is None:
                    session.add(
                        DeploymentLog(
                            deployment_id=deployment.id,
                            sequence=0,
                            stream="combined",
                            message=deployment.log,
                            created_at=datetime.now(UTC),
                        )
                    )
    if "error" in status:
        job.error = str(status["error"])
        if job.kind in {"domain.configure", "domain.renew"}:
            domain_id = job.payload.get("domain_id")
            domain = (
                await session.get(Domain, domain_id)
                if isinstance(domain_id, str)
                else await session.scalar(
                    select(Domain).where(Domain.hostname == job.payload.get("hostname"))
                )
            )
            if domain is not None:
                domain.ssl_status = "failed"
                certificate = await session.scalar(
                    select(SslCertificate).where(SslCertificate.domain_id == domain.id)
                )
                if certificate is not None:
                    certificate.status = "failed"
        if job.kind.startswith("backup.") or job.kind == "database.export":
            backup_id = job.payload.get("backup_id")
            backup = await session.get(Backup, backup_id) if backup_id else None
            if backup is not None:
                backup.status = "failed"
        if job.kind in {
            "project.git_deploy",
            "project.archive_deploy",
            "project.rollback",
        }:
            project_id = job.payload.get("project_id")
            project = await session.get(Project, project_id) if project_id else None
            if project is not None:
                project.status = "failed"
            deployment = await session.scalar(
                select(Deployment)
                .where(Deployment.project_id == project_id)
                .order_by(Deployment.created_at.desc())
            )
            if deployment is not None:
                deployment.status = "failed"
                deployment.log = job.error[-262144:] if job.error else ""
                deployment.finished_at = datetime.now(UTC)
    await session.commit()
    return job


@router.get(
    "/projects/{project_id}/deployments",
    response_model=list[DeploymentResponse],
)
async def list_deployments(
    project_id: str,
    user: CurrentUser,
    session: DbSession,
    limit: int = Query(default=50, ge=1, le=200),
) -> list[Deployment]:
    """List recent deployments for a project."""
    await require_project_permission(session, user, project_id, "project:read")
    result = await session.scalars(
        select(Deployment)
        .where(Deployment.project_id == project_id)
        .order_by(Deployment.created_at.desc())
        .limit(limit)
    )
    return list(result)


@router.get(
    "/projects/{project_id}/deployments/{deployment_id}/log",
    response_model=DeploymentLogResponse,
)
async def get_deployment_log(
    project_id: str,
    deployment_id: str,
    user: CurrentUser,
    session: DbSession,
) -> DeploymentLogResponse:
    """Return the stored combined log for one project deployment."""
    await require_project_permission(session, user, project_id, "project:read")
    deployment = await session.get(Deployment, deployment_id)
    if deployment is None or deployment.project_id != project_id:
        raise HTTPException(status_code=404, detail="Deployment not found")
    return DeploymentLogResponse(deployment_id=deployment.id, log=deployment.log)


async def _authenticate_websocket(
    websocket: WebSocket,
    token: str,
    settings: Settings,
) -> dict[str, Any]:
    """Authenticate a WebSocket and return the access token payload."""
    try:
        return decode_access_token(token, settings)
    except jwt.PyJWTError as exc:
        await websocket.close(code=4401, reason="Unauthorized")
        raise WebSocketDisconnect(code=4401) from exc


async def _authorize_job_websocket(
    websocket: WebSocket,
    job_id: str,
    token: str,
    settings: Settings,
) -> None:
    """Reject unauthorized WebSocket access to project-scoped jobs."""
    payload = await _authenticate_websocket(websocket, token, settings)
    async with SessionFactory() as session:
        user = await session.get(User, payload.get("sub"))
        if user is None or not user.is_active:
            await websocket.close(code=4401, reason="Unauthorized")
            raise WebSocketDisconnect(code=4401)
        job = await session.get(Job, job_id)
        if job is None:
            await websocket.close(code=4404, reason="Job not found")
            raise WebSocketDisconnect(code=4404)
        project_id = job.payload.get("project_id")
        if isinstance(project_id, str):
            try:
                await require_project_permission(session, user, project_id, "project:read")
            except HTTPException as exc:
                await websocket.close(code=4403, reason="Forbidden")
                raise WebSocketDisconnect(code=4403) from exc


@router.websocket("/ws/jobs/{job_id}")
async def job_events(
    websocket: WebSocket,
    job_id: str,
    token: str = Query(min_length=20),
) -> None:
    """Stream queue job state changes until a terminal state."""
    settings = get_settings()
    await _authorize_job_websocket(websocket, job_id, token, settings)
    await websocket.accept()
    previous: dict[str, object] | None = None
    try:
        while True:
            current = task_status(job_id)
            if current != previous:
                await websocket.send_json(current)
                previous = current
            if current["state"] in {"SUCCESS", "FAILURE", "REVOKED"}:
                break
            await asyncio.sleep(1)
    except WebSocketDisconnect:
        return
    finally:
        if websocket.client_state.name == "CONNECTED":
            await websocket.close()
