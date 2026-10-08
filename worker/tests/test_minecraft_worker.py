"""Minecraft durable update, rollback and reservation persistence tests."""

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from app.db.base import Base
from app.models.entities import Job, Project, ProjectRuntime
from nectarine_worker import minecraft, tasks
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

PROJECT_ID = "11111111-1111-1111-1111-111111111111"
JOB_ID = "22222222-2222-2222-2222-222222222222"


@pytest.fixture
def server_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    """Persist one reserved update in an isolated cross-event-loop test database."""
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'minecraft.db'}", poolclass=NullPool
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(tasks, "SessionFactory", factory)
    monkeypatch.setattr(tasks.settings, "storage_root", tmp_path / "storage")
    monkeypatch.setattr(tasks.settings, "field_encryption_key", "")
    monkeypatch.setattr(minecraft, "_notify", lambda *args, **kwargs: None)
    root = tasks.settings.storage_root / "minecraft" / PROJECT_ID
    root.mkdir(parents=True)
    (root / "server.jar").write_bytes(b"original-server")
    (root / "world").mkdir()
    (root / "world" / "level.dat").write_bytes(b"original-world")
    (root / "plugins").mkdir()
    (root / "plugins" / "plugin.jar").write_bytes(b"original-plugin")
    configuration = {
        "server_jar": "server.jar",
        "launch_mode": "jar",
        "java_version": 21,
        "minecraft_version": "1.20.4",
        "build_id": "10",
        "sha256": "old",
        "eula_accepted": True,
        "xms": "1G",
        "xmx": "2G",
        "rcon_enabled": False,
        "game_port": 25565,
    }

    async def initialize() -> None:
        """Create a complete operation reservation without API polling."""
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with factory() as session:
            project = Project(
                id=PROJECT_ID,
                name="Paper",
                slug="paper",
                project_type="minecraft_paper",
                runtime_type="minecraft_paper",
                runtime_config=configuration,
                status="running",
            )
            job = Job(
                id=JOB_ID,
                kind="minecraft.update",
                payload={
                    "project_id": PROJECT_ID,
                    "engine": "paper",
                    "artifact": {},
                    "uploaded_jar": None,
                    "minecraft_version": "1.21.1",
                    "java_version": 21,
                    "updating": True,
                    "phase": "queued",
                },
            )
            session.add_all([project, job])
            await session.flush()
            session.add(
                ProjectRuntime(
                    project_id=PROJECT_ID,
                    runtime_type="minecraft_paper",
                    configuration=configuration,
                    active_job_id=JOB_ID,
                )
            )
            await session.commit()

    asyncio.run(initialize())
    yield factory, root, configuration
    asyncio.run(engine.dispose())


def run_update(
    monkeypatch: pytest.MonkeyPatch,
    root: Path,
    *,
    fail_start: bool = False,
    fail_backup: bool = False,
    was_running: bool = True,
) -> list[str]:
    """Exercise actual backup and rollback while mocking only the host agent."""
    state = {"running": was_running, "starts": 0}
    calls: list[str] = []

    def agent(operation: str, parameters: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        """Model host lifecycle, publication and a failed post-upgrade startup."""
        calls.append(operation)
        if operation == "minecraft_status":
            return {"running": state["running"], "available": state["running"]}
        if operation == "minecraft_stage":
            return {
                "sha256": "new",
                "launcher": "server.jar",
                "launch_mode": "jar",
                "java_version": 21,
                "minecraft_version": "1.21.1",
                "build_id": "22",
                "source": "official",
            }
        if operation == "minecraft_stop":
            state["running"] = False
        if operation == "minecraft_publish":
            (root / "server.jar").write_bytes(b"updated-server")
        if operation == "start_minecraft":
            state["starts"] += 1
            if fail_start and state["starts"] == 1:
                (root / "world" / "level.dat").write_bytes(b"upgraded-world")
                (root / "plugins" / "plugin.jar").write_bytes(b"modified-plugin")
                raise RuntimeError("Startup failed")
            state["running"] = True
        return {}

    monkeypatch.setattr(tasks, "agent_operation", agent)
    if fail_backup:
        from nectarine_worker import runner

        def broken_backup(*args: Any, **kwargs: Any) -> None:
            """Simulate storage exhaustion before any server publication."""
            raise OSError("No space")

        monkeypatch.setattr(runner, "create_backup", broken_backup)
    task = SimpleNamespace(
        request=SimpleNamespace(id=JOB_ID), update_state=lambda **kwargs: None
    )
    if fail_start or fail_backup:
        with pytest.raises(RuntimeError, match="installation failed"):
            minecraft.provision(task, PROJECT_ID)
    else:
        minecraft.provision(task, PROJECT_ID)
    return calls


def stored(factory: Any) -> tuple[Project, Job, ProjectRuntime]:
    """Load committed operation outcomes after the worker returns."""

    async def read() -> tuple[Project, Job, ProjectRuntime]:
        """Read detached persistence entities with an isolated database session."""
        async with factory() as session:
            project = await session.get(Project, PROJECT_ID)
            job = await session.get(Job, JOB_ID)
            runtime = await session.scalar(
                select(ProjectRuntime).where(ProjectRuntime.project_id == PROJECT_ID)
            )
            assert project is not None and job is not None and runtime is not None
            return project, job, runtime

    return asyncio.run(read())


def test_update_persists_without_polling_and_preserves_stopped_state(
    server_store: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stopped server is temporarily validated and stopped again after updating."""
    factory, root, _ = server_store
    calls = run_update(monkeypatch, root, was_running=False)
    project, job, runtime = stored(factory)
    assert job.status == "success"
    assert runtime.active_job_id is None
    assert project.status == "stopped"
    assert project.runtime_config["build_id"] == "22"
    assert (
        calls.index("minecraft_stage")
        < calls.index("minecraft_stop")
        < calls.index("minecraft_publish")
    )
    assert calls.count("minecraft_stop") == 2
    assert list(
        (tasks.settings.storage_root / "backups" / "projects" / PROJECT_ID).glob("*.tar.gz")
    )


def test_failed_start_restores_entire_world_plugins_and_metadata(
    server_store: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Rollback restores the complete recovery point after world files changed."""
    factory, root, configuration = server_store
    calls = run_update(monkeypatch, root, fail_start=True)
    project, job, runtime = stored(factory)
    assert (root / "server.jar").read_bytes() == b"original-server"
    assert (root / "world" / "level.dat").read_bytes() == b"original-world"
    assert (root / "plugins" / "plugin.jar").read_bytes() == b"original-plugin"
    assert project.runtime_config == configuration
    assert project.status == "running"
    assert job.status == "failure"
    assert job.result["recovered"] is True
    assert runtime.active_job_id is None
    assert calls.count("start_minecraft") == 2


def test_failed_backup_never_publishes_new_server(
    server_store: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Storage failures leave the old server tree and resume the previous runtime."""
    factory, root, _ = server_store
    calls = run_update(monkeypatch, root, fail_backup=True)
    assert "minecraft_publish" not in calls
    assert (root / "server.jar").read_bytes() == b"original-server"
    project, job, runtime = stored(factory)
    assert project.status == "running"
    assert job.status == "failure"
    assert runtime.active_job_id is None


def test_execution_lock_prevents_concurrent_recovery(server_store: Any) -> None:
    """Shared filesystem locks fence duplicate delivery and recovery workers."""
    with minecraft.execution_lock(JOB_ID) as acquired:
        assert acquired
        with minecraft.execution_lock(JOB_ID) as second:
            assert not second
    with minecraft.execution_lock(JOB_ID) as acquired:
        assert acquired


def test_initial_install_does_not_require_an_rcon_secret(
    server_store: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A freshly created template can install before configuring RCON or EULA."""
    factory, root, _ = server_store

    async def initial() -> None:
        """Represent an unconfigured template with RCON enabled by default."""
        async with factory() as session:
            project = await session.get(Project, PROJECT_ID)
            job = await session.get(Job, JOB_ID)
            project.runtime_config = {
                **project.runtime_config,
                "rcon_enabled": True,
                "eula_accepted": False,
            }
            job.payload = {**job.payload, "updating": False}
            await session.commit()

    asyncio.run(initial())
    calls = run_update(monkeypatch, root, was_running=False)
    assert "start_minecraft" not in calls
    _, job, runtime = stored(factory)
    assert job.status == "success"
    assert runtime.active_job_id is None


def test_worker_recovery_restores_interrupted_validating_phase(
    server_store: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Recovery after process loss uses persisted backup state instead of polling."""
    from nectarine_worker.runner import create_backup

    factory, root, configuration = server_store
    artifact = (
        tasks.settings.storage_root / "backups" / "projects" / PROJECT_ID / "recovery.tar.gz"
    )
    create_backup(root, artifact, backup_type="project", project_id=PROJECT_ID)
    (root / "world" / "level.dat").write_bytes(b"upgraded-world")
    asyncio.run(
        minecraft.persist_phase(
            JOB_ID,
            "validating",
            80,
            old_configuration=configuration,
            was_running=False,
            backup_path=str(artifact),
        )
    )
    calls = []

    def agent(operation: str, parameters: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        """Simulate a replacement worker reaching a still-running new server."""
        calls.append(operation)
        return {"running": False}

    monkeypatch.setattr(tasks, "agent_operation", agent)
    result = minecraft.recover_reservation(JOB_ID, PROJECT_ID)
    assert result["recovered"] is True
    assert (root / "world" / "level.dat").read_bytes() == b"original-world"
    assert calls[0] == "minecraft_stop"
    assert stored(factory)[2].active_job_id is None


def test_corrupted_recovery_keeps_reservation(
    server_store: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unusable recovery point never re-enables conflicting mutations."""
    factory, root, configuration = server_store
    artifact = (
        tasks.settings.storage_root / "backups" / "projects" / PROJECT_ID / "missing.tar.gz"
    )
    asyncio.run(
        minecraft.persist_phase(
            JOB_ID,
            "publishing",
            65,
            old_configuration=configuration,
            was_running=True,
            backup_path=str(artifact),
        )
    )
    monkeypatch.setattr(tasks, "agent_operation", lambda *args, **kwargs: {"running": False})
    result = minecraft.recover_reservation(JOB_ID, PROJECT_ID)
    assert result["recovered"] is False
    project, job, runtime = stored(factory)
    assert runtime.active_job_id == JOB_ID
    assert job.status == "failure"
    assert project.status == "failed"


def test_failed_save_on_keeps_backup_reservation(
    server_store: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Write resumption failures retain the durable lock until recovery succeeds."""
    factory, _, _ = server_store

    async def initialize_backup() -> None:
        """Replace provisioning metadata with a normal backup reservation."""
        async with factory() as session:
            job = await session.get(Job, JOB_ID)
            job.kind = "backup.create_project"
            job.payload = {"project_id": PROJECT_ID}
            await session.commit()

    asyncio.run(initialize_backup())

    @minecraft.reserved_operation
    def backup(task: Any, **kwargs: Any) -> None:
        """Simulate process failure while the world's save guard is active."""
        asyncio.run(minecraft.persist_phase(JOB_ID, "world_backup", 20))
        raise OSError("Archive failed")

    def agent(*args: Any, **kwargs: Any) -> dict[str, Any]:
        """Simulate an unavailable RCON listener during write resumption."""
        raise RuntimeError("RCON unavailable")

    monkeypatch.setattr(tasks, "agent_operation", agent)
    task = SimpleNamespace(request=SimpleNamespace(id=JOB_ID))
    with pytest.raises(OSError):
        backup(task, project_id=PROJECT_ID)
    assert stored(factory)[2].active_job_id == JOB_ID
    monkeypatch.setattr(tasks, "agent_operation", lambda *args, **kwargs: {"running": False})
    minecraft.recover_reservation(JOB_ID, PROJECT_ID)
    assert stored(factory)[2].active_job_id is None


def test_completed_job_staging_is_cleaned_after_process_loss(
    server_store: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A crash after commit cannot leave terminal job staging on disk indefinitely."""
    import shutil
    from datetime import UTC, datetime, timedelta

    factory, _, _ = server_store
    stage = tasks.settings.storage_root / ".minecraft-staging" / PROJECT_ID / JOB_ID
    stage.mkdir(parents=True)

    async def complete() -> None:
        """Commit completion while simulating a lost staging finalizer."""
        async with factory() as session:
            job = await session.get(Job, JOB_ID)
            runtime = await session.scalar(
                select(ProjectRuntime).where(ProjectRuntime.project_id == PROJECT_ID)
            )
            job.status = "success"
            job.updated_at = datetime.now(UTC) - timedelta(minutes=20)
            runtime.active_job_id = None
            await session.commit()

    asyncio.run(complete())

    def cleanup(operation: str, parameters: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        """Remove only the completed operation's isolated staging directory."""
        assert operation == "minecraft_cleanup"
        assert parameters == {"project_id": PROJECT_ID, "job_id": JOB_ID}
        shutil.rmtree(stage)
        return {"cleaned": True}

    monkeypatch.setattr(tasks, "agent_operation", cleanup)
    assert minecraft.cleanup_finished_jobs() == 1
    assert not stage.exists()
