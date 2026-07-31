"""Worker project cron dispatch tests."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from app.db.base import Base
from app.models.entities import CronJob, EnvironmentVariable, Project
from nectarine_worker import tasks
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


def _project(project_id: str, name: str, runtime_type: str) -> Project:
    """Create a minimal persisted project for cron dispatch tests."""
    return Project(
        id=project_id,
        name=name,
        slug=name.lower().replace(" ", "-"),
        project_type="minecraft_forge" if runtime_type == "minecraft_forge" else "backend",
        runtime_type=runtime_type,
        runtime_config={"rcon_enabled": True, "rcon_host_port": 25575}
        if runtime_type == "minecraft_forge"
        else {"cron_service": "worker"}
        if runtime_type == "docker_compose"
        else {},
    )


def _cron(project_id: str, name: str, command: str) -> CronJob:
    """Create a due cron job."""
    return CronJob(
        project_id=project_id,
        name=name,
        expression="* * * * *",
        command=command,
        enabled=True,
        last_run_at=datetime.now(UTC) - timedelta(minutes=2),
    )


@pytest.mark.asyncio
async def test_due_cron_dispatches_by_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Due cron jobs use runtime-specific allowlisted agent operations."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'cron.db'}")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    project_ids = {
        "docker": "11111111-1111-1111-1111-111111111111",
        "compose": "22222222-2222-2222-2222-222222222222",
        "systemd": "33333333-3333-3333-3333-333333333333",
        "minecraft": "44444444-4444-4444-4444-444444444444",
    }
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with session_factory() as session:
        projects = [
            _project(project_ids["docker"], "Docker App", "docker"),
            _project(project_ids["compose"], "Compose App", "docker_compose"),
            _project(project_ids["systemd"], "Systemd App", "systemd"),
            _project(project_ids["minecraft"], "Minecraft App", "minecraft_forge"),
        ]
        session.add_all(projects)
        session.add_all(
            [
                _cron(projects[0].id, "docker cron", "python task.py"),
                _cron(projects[1].id, "compose cron", "python task.py"),
                _cron(projects[2].id, "systemd cron", "python task.py"),
                _cron(projects[3].id, "minecraft cron", "say backup"),
                EnvironmentVariable(
                    project_id=projects[3].id,
                    key="MINECRAFT_RCON_PASSWORD",
                    encrypted_value="rcon-secret",
                    is_secret=True,
                ),
            ]
        )
        await session.commit()
    calls: list[tuple[str, dict[str, Any], float]] = []

    def fake_agent_operation(
        operation: str,
        parameters: dict[str, Any] | None = None,
        *,
        timeout: float = 30,
    ) -> dict[str, Any]:
        """Capture scheduled agent operations without touching the host."""
        calls.append((operation, parameters or {}, timeout))
        return {"ok": True}

    monkeypatch.setattr(tasks, "SessionFactory", session_factory)
    monkeypatch.setattr(tasks, "agent_operation", fake_agent_operation)

    result = await tasks.execute_due_cron_jobs()

    async with session_factory() as session:
        statuses = [
            row.last_status
            for row in await session.scalars(select(CronJob).order_by(CronJob.name))
        ]
    await engine.dispose()
    assert result == {"executed": 4, "failed": 0}
    assert statuses == ["success", "success", "success", "success"]
    assert {call[0]: call[1] for call in calls} == {
        "container_command": {
            "container": f"vps-project-{project_ids['docker']}",
            "command": "python task.py",
        },
        "compose_command": {
            "project_id": project_ids["compose"],
            "service": "worker",
            "command": "python task.py",
        },
        "run_project_command": {
            "project_id": project_ids["systemd"],
            "command": "python task.py",
        },
        "minecraft_command": {
            "project_id": project_ids["minecraft"],
            "rcon_host_port": 25575,
            "rcon_password": "rcon-secret",
            "command": "say backup",
        },
    }


@pytest.mark.asyncio
async def test_due_cron_marks_static_runtime_unsupported(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Static projects do not run arbitrary host cron commands."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'cron-static.db'}")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    project_id = "55555555-5555-5555-5555-555555555555"
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with session_factory() as session:
        project = _project(project_id, "Static Site", "static")
        session.add(project)
        session.add(_cron(project.id, "static cron", "echo unsafe"))
        await session.commit()
    monkeypatch.setattr(tasks, "SessionFactory", session_factory)
    monkeypatch.setattr(
        tasks,
        "agent_operation",
        lambda *args, **kwargs: pytest.fail("static cron must not call agent"),
    )

    result = await tasks.execute_due_cron_jobs()

    async with session_factory() as session:
        status = await session.scalar(select(CronJob.last_status))
    await engine.dispose()
    assert result == {"executed": 0, "failed": 1}
    assert status == "unsupported"
