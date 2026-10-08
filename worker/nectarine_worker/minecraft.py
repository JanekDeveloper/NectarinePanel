"""Durable Minecraft provisioning, rollback and interrupted-job recovery."""

import asyncio
import contextlib
import fcntl
import json
import logging
import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from app.models.entities import Backup, Job, Project, ProjectRuntime
from app.services.minecraft import minecraft_parameters
from app.services.minecraft_operations import release_minecraft_job
from celery.signals import worker_ready
from sqlalchemy import select, update

from nectarine_worker.celery_app import celery_app


@contextmanager
def execution_lock(job_id: str) -> Iterator[bool]:
    """Prevent duplicate workers and recovery from operating on the same job."""
    import uuid

    from nectarine_worker.tasks import settings

    uuid.UUID(job_id)
    directory = settings.storage_root / ".minecraft-locks"
    directory.mkdir(parents=True, exist_ok=True, mode=0o750)
    descriptor = os.open(
        directory / f"{job_id}.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o640
    )
    acquired = False
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
        except BlockingIOError:
            pass
        yield acquired
    finally:
        os.close(descriptor)


async def job_context(job_id: str, project_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """Load only the job that still owns the project's reservation."""
    from nectarine_worker.tasks import SessionFactory, settings

    async with SessionFactory() as session:
        project = await session.get(Project, project_id)
        job = await session.get(Job, job_id)
        runtime = await session.scalar(
            select(ProjectRuntime).where(ProjectRuntime.project_id == project_id)
        )
        if project is None or job is None or runtime is None or runtime.active_job_id != job_id:
            raise ValueError("Minecraft operation reservation no longer exists")
        parameters = await minecraft_parameters(project, session, settings, require_rcon=False)
        return dict(job.payload), {
            "configuration": dict(project.runtime_config),
            "parameters": parameters,
        }


async def persist_phase(job_id: str, phase: str, progress: int, **recovery: Any) -> None:
    """Write recovery context before filesystem mutations and refresh heartbeat."""
    from nectarine_worker.tasks import SessionFactory

    async with SessionFactory() as session:
        job = await session.get(Job, job_id)
        if job is None:
            raise ValueError("Minecraft job no longer exists")
        job.payload = {
            **job.payload,
            "phase": phase,
            "recovery": {**job.payload.get("recovery", {}), **recovery},
        }
        job.status = "started"
        job.progress = progress
        await session.commit()


async def finish_job(
    job_id: str,
    project_id: str,
    *,
    result: dict[str, Any] | None = None,
    error: str | None = None,
    configuration: dict[str, Any] | None = None,
    status: str | None = None,
    release: bool = True,
) -> None:
    """Commit runtime state and a sanitized terminal job outcome together."""
    from nectarine_worker.tasks import SessionFactory

    async with SessionFactory() as session:
        job = await session.get(Job, job_id)
        project = await session.get(Project, project_id)
        runtime = await session.scalar(
            select(ProjectRuntime).where(ProjectRuntime.project_id == project_id)
        )
        if project is None or job is None or runtime is None or runtime.active_job_id != job_id:
            raise ValueError("Minecraft reservation changed")
        if configuration is not None:
            project.runtime_config = configuration
            runtime.configuration = configuration
        if status is not None:
            project.status = status
        job.status = "failure" if error else "success"
        job.error = error
        job.result = result
        job.progress = 100
        backup_id = job.payload.get("recovery", {}).get("backup_id")
        if error and backup_id:
            backup = await session.get(Backup, backup_id)
            if backup is not None and backup.status == "queued":
                backup.status = "failed"
        if release:
            await release_minecraft_job(session, project_id, job_id)
        await session.commit()


async def create_update_backup_record(project_id: str, job_id: str, root: Path) -> str:
    """Reserve a discoverable full backup before recording its ready artifact."""
    from nectarine_worker.tasks import SessionFactory

    async with SessionFactory() as session:
        backup = Backup(project_id=project_id, backup_type="project", status="queued")
        session.add(backup)
        await session.flush()
        backup_id = backup.id
        job = await session.get(Job, job_id)
        if job is None:
            raise ValueError("Minecraft job missing")
        job.payload = {
            **job.payload,
            "recovery": {**job.payload.get("recovery", {}), "backup_id": backup_id},
        }
        await session.commit()
        return backup_id


@contextmanager
def heartbeat(job_id: str) -> Iterator[None]:
    """Keep durable operation timestamps current until the worker exits."""
    stop = threading.Event()

    async def touch() -> None:
        """Refresh only the durable timestamp without overwriting phase data."""
        from nectarine_worker.tasks import SessionFactory

        async with SessionFactory() as session:
            await session.execute(
                update(Job).where(Job.id == job_id).values(updated_at=datetime.now(UTC))
            )
            await session.commit()

    def run() -> None:
        """Refresh the lease periodically while respecting task shutdown."""
        while not stop.wait(30):
            with contextlib.suppress(Exception):
                asyncio.run(touch())

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join(timeout=5)


def stop_parameters(parameters: dict[str, Any]) -> dict[str, Any]:
    """Select only fields accepted by the graceful stop operation."""
    return {key: parameters[key] for key in ("project_id", "rcon_host_port", "rcon_password")}


def provision(task: Any, project_id: str) -> dict[str, Any]:
    """Stage, back up and publish a server with complete rollback on failure."""
    from nectarine_worker.runner import create_backup, sha256_file, validate_backup_archive
    from nectarine_worker.tasks import agent_operation, mark_backup_ready, settings

    job_id = str(task.request.id)
    payload, context = asyncio.run(job_context(job_id, project_id))
    if payload.get("phase") != "queued":
        return recover(job_id, project_id)
    root = settings.storage_root / "minecraft" / project_id
    old_config = context["configuration"]
    parameters = context["parameters"]
    try:
        running = bool(
            agent_operation(
                "minecraft_status",
                {"project_id": project_id, "game_port": int(parameters["game_port"])},
            ).get("running")
        )
        asyncio.run(
            persist_phase(
                job_id, "staging", 10, old_configuration=old_config, was_running=running
            )
        )
        task.update_state(state="PROGRESS", meta={"progress": 10, "stage": "staging"})
        staged = agent_operation(
            "minecraft_stage",
            {
                "project_id": project_id,
                "job_id": job_id,
                "engine": payload["engine"],
                "artifact": payload["artifact"],
                "uploaded_jar": payload["uploaded_jar"],
                "java_version": payload["java_version"],
            },
            timeout=2500,
        )
        if payload.get("minecraft_version"):
            staged["minecraft_version"] = payload["minecraft_version"]
        new_config = {
            **old_config,
            **staged,
            "server_jar": staged["launcher"],
            "build_id": staged.get("build_id"),
            "java_version": int(staged.get("java_version", payload["java_version"])),
        }
        asyncio.run(persist_phase(job_id, "stopping", 40, new_configuration=new_config))
        if payload["updating"]:
            agent_operation("minecraft_stop", stop_parameters(parameters), timeout=150)
            backup_id = asyncio.run(create_update_backup_record(project_id, job_id, root))
            asyncio.run(persist_phase(job_id, "backup", 50))
            target = (
                settings.storage_root
                / "backups"
                / "projects"
                / project_id
                / f"update-{job_id}.tar.gz"
            )
            manifest = create_backup(
                root,
                target,
                backup_type="project",
                project_id=project_id,
                encryption_key=settings.field_encryption_key or None,
            )
            artifact_path = Path(manifest["artifact_path"])
            if sha256_file(artifact_path) != manifest["checksum"]:
                raise ValueError("Update backup checksum mismatch")
            validate_backup_archive(artifact_path, encryption_key=settings.field_encryption_key)
            # Runtime metadata belongs to the same recovery point as the files.
            manifest["minecraft_configuration"] = old_config
            artifact_path.with_suffix(artifact_path.suffix + ".manifest.json").write_text(
                json.dumps(manifest), encoding="utf-8"
            )
            asyncio.run(mark_backup_ready(backup_id, manifest, artifact_path))
            asyncio.run(persist_phase(job_id, "publishing", 65, backup_path=str(artifact_path)))
        else:
            if running:
                raise ValueError("A new server cannot replace a running container")
            asyncio.run(persist_phase(job_id, "publishing", 65))
        agent_operation(
            "minecraft_publish",
            {"project_id": project_id, "job_id": job_id, "sha256": staged["sha256"]},
        )
        if payload["updating"]:
            asyncio.run(persist_phase(job_id, "validating", 80))
            launch = {
                **parameters,
                **{
                    key: new_config[key]
                    for key in ("java_version", "server_jar", "launch_mode")
                },
                "wait_ready": True,
                "server_sha256": new_config["sha256"],
            }
            agent_operation("start_minecraft", launch, timeout=390)
            if not running:
                agent_operation("minecraft_stop", stop_parameters(parameters), timeout=150)
        result = {
            "project_id": project_id,
            "sha256": staged["sha256"],
            "build_id": staged.get("build_id"),
            "installed": True,
        }
        asyncio.run(
            finish_job(
                job_id,
                project_id,
                result=result,
                configuration=new_config,
                status="running" if running else "stopped",
            )
        )
        _notify(
            project_id,
            successful=True,
            operation="update" if payload["updating"] else "installation",
        )
        return result
    except Exception:
        recover(job_id, project_id)
        raise RuntimeError(
            "Minecraft installation failed; check the persisted recovery result"
        ) from None
    finally:
        with contextlib.suppress(Exception):
            agent_operation("minecraft_cleanup", {"project_id": project_id, "job_id": job_id})


def recover(job_id: str, project_id: str) -> dict[str, Any]:
    """Restore the whole pre-update recovery point before releasing reservation."""
    from nectarine_worker.runner import restore_backup
    from nectarine_worker.tasks import agent_operation, settings

    payload, context = asyncio.run(job_context(job_id, project_id))
    recovery = payload.get("recovery", {})
    phase = payload.get("phase", "queued")
    parameters = context["parameters"]
    old_config = recovery.get("old_configuration", context["configuration"])
    root = settings.storage_root / "minecraft" / project_id
    try:
        if phase in {"publishing", "validating", "recovering"} and payload.get("updating"):
            asyncio.run(persist_phase(job_id, "recovering", 90))
            agent_operation("minecraft_stop", stop_parameters(parameters), timeout=150)
            archive = Path(recovery["backup_path"])
            allowed = settings.storage_root / "backups" / "projects" / project_id
            if archive.resolve().parent != allowed.resolve():
                raise ValueError("Invalid Minecraft recovery backup path")
            restore_backup(
                archive,
                root,
                archive.with_suffix(archive.suffix + ".manifest.json"),
                encryption_key=settings.field_encryption_key,
                expected_project_id=project_id,
            )
        if recovery.get("was_running") and phase not in {"queued", "staging"}:
            launch = {
                **parameters,
                **{
                    key: old_config[key]
                    for key in ("java_version", "server_jar", "launch_mode")
                    if key in old_config
                },
                "wait_ready": True,
            }
            status = agent_operation(
                "minecraft_status",
                {"project_id": project_id, "game_port": int(parameters["game_port"])},
            )
            if not status.get("running"):
                agent_operation("start_minecraft", launch, timeout=390)
        result = {"project_id": project_id, "recovered": True, "phase": phase}
        asyncio.run(
            finish_job(
                job_id,
                project_id,
                result=result,
                error="Minecraft operation failed; previous state restored",
                configuration=old_config,
                status=("running" if recovery["was_running"] else "stopped")
                if "was_running" in recovery
                else None,
            )
        )
    except Exception:
        # Keep the reservation when recovery cannot complete; never enable writes
        # over an uncertain recovery point or automatically kill the server.
        result = {"project_id": project_id, "recovered": False, "phase": phase}
        with contextlib.suppress(Exception):
            agent_operation("minecraft_stop", stop_parameters(parameters), timeout=150)
        asyncio.run(
            finish_job(
                job_id,
                project_id,
                result=result,
                error="Minecraft recovery failed; manual intervention required",
                status="failed",
                release=False,
            )
        )
    with contextlib.suppress(Exception):
        agent_operation("minecraft_cleanup", {"project_id": project_id, "job_id": job_id})
    _notify(
        project_id, successful=False, operation="recovery", recovered=bool(result["recovered"])
    )
    return result


def _notify(
    project_id: str, *, successful: bool, operation: str = "operation", recovered: bool = False
) -> None:
    """Deliver sanitized persistent and Telegram operation notifications."""
    from nectarine_worker.notifications import create_owner_alert

    with contextlib.suppress(Exception):
        asyncio.run(
            create_owner_alert(
                notification_type="minecraft.success" if successful else "minecraft.failed",
                severity="success" if successful else "error",
                title="Minecraft",
                message=f"Minecraft {operation} completed."
                if successful
                else f"Minecraft {operation} failed. "
                + (
                    "Previous state restored."
                    if recovered
                    else "Check the job recovery result."
                ),
                resource_type="project",
                resource_id=project_id,
                deduplicate=False,
            )
        )


@celery_app.task(
    bind=True, name="minecraft.provision", acks_late=True, reject_on_worker_lost=True
)  # type: ignore[untyped-decorator]
def provision_server(task: Any, *, project_id: str) -> dict[str, Any]:
    """Execute one provision job exclusively with a durable heartbeat."""
    job_id = str(task.request.id)
    with execution_lock(job_id) as acquired:
        if not acquired:
            raise RuntimeError("Minecraft job is already executing")
        with heartbeat(job_id):
            try:
                return provision(task, project_id)
            except Exception:

                async def fail_unstarted_job() -> None:
                    """Release only a job that failed before starting any mutation."""
                    from nectarine_worker.tasks import SessionFactory

                    async with SessionFactory() as session:
                        job = await session.get(Job, job_id)
                        if job is not None and job.payload.get("phase") == "queued":
                            await finish_job(
                                job_id, project_id, error="Minecraft preparation failed"
                            )

                asyncio.run(fail_unstarted_job())
                raise RuntimeError(
                    "Minecraft operation failed; inspect recovery result"
                ) from None


async def interrupted_jobs() -> list[tuple[str, str]]:
    """Find reserved operations whose heartbeat stopped before completion."""
    from nectarine_worker.tasks import SessionFactory

    async with SessionFactory() as session:
        rows = await session.execute(
            select(Job.id, ProjectRuntime.project_id)
            .join(ProjectRuntime, ProjectRuntime.active_job_id == Job.id)
            .where(
                Job.status.in_(("queued", "started")),
                Job.updated_at < datetime.now(UTC) - timedelta(seconds=600),
            )
        )
        return [(str(job_id), str(project_id)) for job_id, project_id in rows]


@celery_app.task(name="minecraft.recover")  # type: ignore[untyped-decorator]
def recover_interrupted_servers() -> dict[str, int]:
    """Recover orphaned updates periodically and after worker startup."""
    count = 0
    for job_id, project_id in asyncio.run(interrupted_jobs()):
        with execution_lock(job_id) as acquired:
            if acquired:
                with heartbeat(job_id):
                    recover_reservation(job_id, project_id)
                count += 1
    return {"recovered": count, "cleaned": cleanup_finished_jobs()}


def cleanup_finished_jobs() -> int:
    """Clean job-owned staging when a completed worker died before its finalizer."""
    from nectarine_worker.tasks import SessionFactory, agent_operation, settings

    async def finished() -> list[tuple[str, str]]:
        """Select terminal provision jobs with no remaining runtime reservation."""
        async with SessionFactory() as session:
            jobs = await session.scalars(
                select(Job).where(
                    Job.kind.in_(("minecraft.install", "minecraft.update")),
                    Job.status.in_(("success", "failure")),
                    Job.updated_at < datetime.now(UTC) - timedelta(seconds=600),
                )
            )
            active = set(
                await session.scalars(
                    select(ProjectRuntime.active_job_id).where(
                        ProjectRuntime.active_job_id.is_not(None)
                    )
                )
            )
            return [
                (job.id, str(job.payload["project_id"]))
                for job in jobs
                if job.id not in active and "engine" in job.payload
            ]

    count = 0
    for job_id, project_id in asyncio.run(finished()):
        path = settings.storage_root / ".minecraft-staging" / project_id / job_id
        if not path.exists() or path.is_symlink():
            continue
        with execution_lock(job_id) as acquired:
            if acquired:
                try:
                    agent_operation(
                        "minecraft_cleanup", {"project_id": project_id, "job_id": job_id}
                    )
                    count += 1
                except Exception:
                    logging.getLogger(__name__).warning(
                        "Minecraft staging cleanup failed for job %s", job_id
                    )
    return count


@worker_ready.connect  # type: ignore[untyped-decorator]
def queue_recovery(sender: Any = None, **kwargs: Any) -> None:
    """Request orphan recovery when a worker is ready to receive tasks."""
    del sender, kwargs
    recover_interrupted_servers.delay()


def reserved_operation(function: Any) -> Any:
    """Finalize reservations for existing backup, restore and file worker tasks."""
    from functools import wraps

    @wraps(function)
    def wrapped(task: Any, *args: Any, **kwargs: Any) -> Any:
        """Execute reserved Minecraft work while leaving other tasks unchanged."""
        from nectarine_worker.tasks import SessionFactory

        job_id = getattr(getattr(task, "request", None), "id", None)
        if not job_id:
            return function(task, *args, **kwargs)

        async def reserved_project() -> str | None:
            """Find the project whose reservation belongs to this task."""
            async with SessionFactory() as session:
                value = await session.scalar(
                    select(ProjectRuntime.project_id).where(
                        ProjectRuntime.active_job_id == job_id
                    )
                )
                job = await session.get(Job, str(job_id))
                if value is None and job is not None:
                    candidate = job.payload.get("project_id")
                    project = await session.get(Project, candidate) if candidate else None
                    if project is not None and project.runtime_type.startswith("minecraft_"):
                        raise RuntimeError("Minecraft operation reservation no longer exists")
                return str(value) if value is not None else None

        project_id = asyncio.run(reserved_project())
        if not project_id:
            return function(task, *args, **kwargs)
        with execution_lock(str(job_id)) as acquired:
            if not acquired:
                raise RuntimeError("Minecraft operation already executing")
            with heartbeat(str(job_id)):
                asyncio.run(
                    persist_phase(
                        str(job_id), "working", 5, operation=function.__name__, arguments=kwargs
                    )
                )
                try:
                    result = function(task, *args, **kwargs)
                except BaseException:
                    release = True
                    try:
                        payload, context = asyncio.run(job_context(str(job_id), project_id))
                        if payload.get("phase") == "world_backup":
                            from nectarine_worker.tasks import agent_operation

                            agent_operation(
                                "minecraft_backup",
                                {**stop_parameters(context["parameters"]), "action": "resume"},
                            )
                    except Exception:
                        release = False
                    asyncio.run(
                        finish_job(
                            str(job_id),
                            project_id,
                            error="Minecraft operation failed",
                            release=release,
                        )
                    )
                    _notify(project_id, successful=False, operation=function.__name__)
                    raise
                else:
                    asyncio.run(finish_job(str(job_id), project_id, result=result))
                    if function.__name__ in {
                        "restore_project_backup",
                        "install_minecraft_server",
                    }:
                        _notify(
                            project_id,
                            successful=True,
                            operation="restore"
                            if function.__name__ == "restore_project_backup"
                            else "installation",
                        )
                    return result

    return wrapped


def _recover_reserved_job(job_id: str, project_id: str) -> dict[str, Any]:
    """Recover interrupted saves or reconcile completed synchronous operations."""
    from nectarine_worker.tasks import agent_operation, restore_project_backup

    payload, context = asyncio.run(job_context(job_id, project_id))
    if "engine" in payload:
        return recover(job_id, project_id)
    agent_operation("minecraft_fence", {"project_id": project_id}, timeout=150)
    phase = payload.get("phase")
    operation = payload.get("recovery", {}).get("operation")
    if operation == "install_minecraft_server":
        agent_operation("minecraft_cleanup", {"project_id": project_id, "job_id": job_id})
    if operation == "restore_project_backup":
        from types import SimpleNamespace

        task = SimpleNamespace(
            request=SimpleNamespace(id=job_id), update_state=lambda **kwargs: None
        )
        try:
            result = restore_project_backup.run.__wrapped__(
                task, **payload["recovery"]["arguments"]
            )
            asyncio.run(finish_job(job_id, project_id, result=result, status="stopped"))
            _notify(project_id, successful=True)
            return dict(result)
        except Exception:
            asyncio.run(
                finish_job(
                    job_id,
                    project_id,
                    error="Minecraft restore recovery failed; manual intervention required",
                    status="failed",
                    release=False,
                )
            )
            _notify(project_id, successful=False)
            return {"project_id": project_id, "recovered": False}
    if phase == "world_backup":
        agent_operation(
            "minecraft_backup", {**stop_parameters(context["parameters"]), "action": "resume"}
        )
    result = {"project_id": project_id, "interrupted": True}
    status = agent_operation(
        "minecraft_status",
        {"project_id": project_id, "game_port": int(context["parameters"]["game_port"])},
    )
    asyncio.run(
        finish_job(
            job_id,
            project_id,
            result=result,
            error="Interrupted Minecraft operation; inspect server state",
            status="running" if status.get("running") else "stopped",
        )
    )
    _notify(project_id, successful=False)
    return result


def recover_reservation(job_id: str, project_id: str) -> dict[str, Any]:
    """Keep failed generic recovery fenced and expose a sanitized retry outcome."""
    try:
        return _recover_reserved_job(job_id, project_id)
    except Exception:
        asyncio.run(
            finish_job(
                job_id,
                project_id,
                result={"project_id": project_id, "recovered": False},
                error="Minecraft recovery failed; manual intervention required",
                status="failed",
                release=False,
            )
        )
        _notify(project_id, successful=False)
        return {"project_id": project_id, "recovered": False}


@celery_app.task(bind=True, name="minecraft.recover_one")  # type: ignore[untyped-decorator]
def recover_one_server(task: Any, *, project_id: str) -> dict[str, Any]:
    """Retry a failed recovery only while holding the original execution lock."""
    job_id = str(task.request.id)
    with execution_lock(job_id) as acquired:
        if not acquired:
            raise RuntimeError("Minecraft operation still executing")
        with heartbeat(job_id):
            return recover_reservation(job_id, project_id)
