"""Worker managed-database lifecycle tests."""

import asyncio
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from app.db.base import Base
from app.models.entities import (
    Backup,
    BackupPolicy,
    DatabaseInstance,
    DatabaseServer,
    DatabaseUser,
    EnvironmentVariable,
    Job,
    MetricSample,
    Notification,
    Project,
    ProjectResourcePolicy,
)
from app.services.databases import connection_string
from nectarine_worker import tasks
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool


class FakePostgresConnection:
    """Capture fixed PostgreSQL administrative statements."""

    def __init__(self) -> None:
        """Initialize captured statements."""
        self.statements: list[tuple[str, tuple[object, ...]]] = []
        self.closed = False

    async def execute(self, statement: str, *parameters: object) -> str:
        """Capture one statement and its bound parameters."""
        self.statements.append((statement, parameters))
        return "OK"

    async def close(self) -> None:
        """Record connection closure."""
        self.closed = True


def test_worker_database_engine_does_not_reuse_cross_loop_connections() -> None:
    """Celery workers use fresh async DB connections for each event loop."""
    assert isinstance(tasks.worker_engine.sync_engine.pool, NullPool)


def test_directory_size_tolerates_protected_imported_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Local metric collection defers DAC-protected roots to the system agent."""
    protected = tmp_path / "protected"
    original_exists = Path.exists

    def deny_protected_path(path: Path) -> bool:
        """Raise a DAC error only for the simulated imported root."""
        if path == protected:
            raise PermissionError
        return original_exists(path)

    monkeypatch.setattr(Path, "exists", deny_protected_path)

    assert tasks._directory_size(protected) == 0


class FakeMysqlCursor:
    """Capture fixed MySQL administrative statements."""

    def __init__(self) -> None:
        """Initialize captured statements."""
        self.statements: list[str] = []

    async def __aenter__(self) -> "FakeMysqlCursor":
        """Enter the asynchronous cursor context."""
        return self

    async def __aexit__(self, *args: object) -> None:
        """Exit the asynchronous cursor context."""
        del args

    async def execute(self, statement: str) -> None:
        """Capture one fixed administrative statement."""
        self.statements.append(statement)


class FakeMysqlConnection:
    """Minimal asynchronous MySQL connection double."""

    def __init__(self) -> None:
        """Initialize cursor and closure state."""
        self.cursor_instance = FakeMysqlCursor()
        self.closed = False

    def cursor(self) -> FakeMysqlCursor:
        """Return the deterministic cursor."""
        return self.cursor_instance

    def close(self) -> None:
        """Record connection closure."""
        self.closed = True


@pytest.mark.asyncio
async def test_delete_postgresql_uses_bound_database_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PostgreSQL deletion binds lookups and quotes validated identifiers."""
    connection = FakePostgresConnection()

    async def fake_connect(**kwargs: object) -> FakePostgresConnection:
        """Return the deterministic administrative connection."""
        assert kwargs["database"] == "postgres"
        return connection

    monkeypatch.setattr(tasks.asyncpg, "connect", fake_connect)
    server = DatabaseServer(
        name="PostgreSQL",
        engine="postgresql",
        host="postgres",
        port=5432,
        admin_username="postgres",
        encrypted_admin_password="admin",
        tls_enabled=False,
    )
    instance = DatabaseInstance(
        server_id="server-id",
        name="example_app",
        username="example_user",
        encrypted_password="password",
        engine="postgresql",
        status="ready",
    )
    await tasks._delete_postgresql(server, instance, "admin-password")
    assert connection.statements == [
        (
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = $1 AND pid <> pg_backend_pid()",
            ("example_app",),
        ),
        ('DROP DATABASE IF EXISTS "example_app"', ()),
        ('DROP ROLE IF EXISTS "example_user"', ()),
    ]
    assert connection.closed is True


@pytest.mark.asyncio
async def test_delete_mysql_drops_database_and_dedicated_user(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MySQL deletion uses only validated identifiers in fixed statements."""
    connection = FakeMysqlConnection()

    async def fake_connect(**kwargs: object) -> FakeMysqlConnection:
        """Return the deterministic MySQL administrative connection."""
        assert kwargs["autocommit"] is True
        return connection

    monkeypatch.setattr(tasks.asyncmy, "connect", fake_connect)
    server = DatabaseServer(
        name="MySQL",
        engine="mysql",
        host="mysql",
        port=3306,
        admin_username="root",
        encrypted_admin_password="admin",
        tls_enabled=False,
    )
    instance = DatabaseInstance(
        server_id="server-id",
        name="example_app",
        username="example_user",
        encrypted_password="password",
        engine="mysql",
        status="ready",
    )
    await tasks._delete_mysql(server, instance, "admin-password")
    assert connection.cursor_instance.statements == [
        "DROP DATABASE IF EXISTS `example_app`",
        "DROP USER IF EXISTS 'example_user'@'%'",
    ]
    assert connection.closed is True


@pytest.mark.asyncio
async def test_delete_database_removes_only_matching_project_dsn(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Successful remote deletion removes metadata and its exact attached DSN."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'database-delete.db'}")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with session_factory() as session:
        project = Project(
            name="Delete Project",
            slug="delete-project",
            project_type="backend",
            runtime_type="docker",
        )
        server = DatabaseServer(
            name="PostgreSQL",
            engine="postgresql",
            host="postgres",
            port=5432,
            admin_username="postgres",
            encrypted_admin_password="admin-password",
            tls_enabled=False,
        )
        session.add_all([project, server])
        await session.flush()
        database = DatabaseInstance(
            server_id=server.id,
            project_id=project.id,
            name="example_app",
            username="example_user",
            encrypted_password="database-password",
            engine="postgresql",
            status="ready",
        )
        session.add(database)
        await session.flush()
        database_id = database.id
        session.add(
            DatabaseUser(
                database_id=database.id,
                username=database.username,
                encrypted_password=database.encrypted_password,
                privileges=["ALL"],
            )
        )
        session.add(
            EnvironmentVariable(
                project_id=project.id,
                key="DATABASE_URL",
                encrypted_value=connection_string(
                    engine="postgresql",
                    host="postgres",
                    port=5432,
                    database="example_app",
                    username="example_user",
                    password="database-password",
                ),
                is_secret=True,
            )
        )
        await session.commit()

    async def fake_remote_delete(*args: Any, **kwargs: Any) -> None:
        """Replace the external database operation."""
        del args, kwargs

    monkeypatch.setattr(tasks, "SessionFactory", session_factory)
    monkeypatch.setattr(tasks, "_delete_postgresql", fake_remote_delete)
    result = await tasks.delete_database_record(database_id)
    async with session_factory() as session:
        database = await session.get(DatabaseInstance, database_id)
        user = await session.scalar(
            select(DatabaseUser).where(DatabaseUser.database_id == database_id)
        )
        variable = await session.scalar(
            select(EnvironmentVariable).where(EnvironmentVariable.key == "DATABASE_URL")
        )
    await engine.dispose()
    assert result == {"database_id": database_id, "deleted": True}
    assert database is None
    assert user is None
    assert variable is None


@pytest.mark.asyncio
async def test_delete_database_preserves_metadata_after_remote_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A remote deletion failure retains metadata with an actionable status."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'database-failure.db'}")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with session_factory() as session:
        server = DatabaseServer(
            name="PostgreSQL",
            engine="postgresql",
            host="postgres",
            port=5432,
            admin_username="postgres",
            encrypted_admin_password="admin-password",
            tls_enabled=False,
        )
        session.add(server)
        await session.flush()
        database = DatabaseInstance(
            server_id=server.id,
            name="example_app",
            username="example_user",
            encrypted_password="database-password",
            engine="postgresql",
            status="ready",
        )
        session.add(database)
        await session.commit()
        database_id = database.id

    async def failing_remote_delete(*args: Any, **kwargs: Any) -> None:
        """Emulate an unavailable database server."""
        del args, kwargs
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(tasks, "SessionFactory", session_factory)
    monkeypatch.setattr(tasks, "_delete_postgresql", failing_remote_delete)
    with pytest.raises(RuntimeError, match="database unavailable"):
        await tasks.delete_database_record(database_id)
    async with session_factory() as session:
        database = await session.get(DatabaseInstance, database_id)
        assert database is not None
        status = database.status
    await engine.dispose()
    assert status == "delete_failed"


@pytest.mark.asyncio
async def test_sqlite_lifecycle_uses_consistent_backup_and_atomic_restore(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SQLite provisioning, backup, restore, and deletion stay panel-contained."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'sqlite-lifecycle.db'}")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with session_factory() as session:
        server = DatabaseServer(
            name="Local SQLite",
            engine="sqlite",
            host="local",
            port=0,
            admin_username="",
            encrypted_admin_password="",
            tls_enabled=False,
        )
        session.add(server)
        await session.flush()
        database = DatabaseInstance(
            server_id=server.id,
            name="local_app",
            username="sqlite",
            encrypted_password="",
            engine="sqlite",
            status="queued",
        )
        session.add(database)
        await session.commit()
        database_id = database.id
    monkeypatch.setattr(tasks, "SessionFactory", session_factory)
    monkeypatch.setattr(tasks.settings, "storage_root", tmp_path / "storage")
    provisioned = await tasks.provision_database_record(database_id)
    database_path = tasks.sqlite_database_path(tasks.settings.storage_root, database_id)
    assert provisioned["status"] == "ready"
    assert database_path.stat().st_mode & 0o777 == 0o640
    with sqlite3.connect(database_path) as connection:
        connection.execute("CREATE TABLE values_table (value TEXT NOT NULL)")
        connection.execute("INSERT INTO values_table VALUES ('original')")
        connection.commit()
    async with session_factory() as session:
        backup = Backup(
            database_id=database_id,
            backup_type="database",
            status="queued",
        )
        session.add(backup)
        await session.commit()
        backup_id = backup.id
    monkeypatch.setattr(tasks.export_database, "update_state", lambda **kwargs: None)
    exported = await asyncio.to_thread(
        tasks.export_database.run,
        database_id=database_id,
        backup_id=backup_id,
    )
    backup_path = Path(str(exported["path"]))
    async with session_factory() as session:
        backup = await session.get(Backup, backup_id)
        assert backup is not None
        backup_status = backup.status
    assert backup_status == "ready"
    assert backup_path.suffix == ".sqlite3"
    with sqlite3.connect(database_path) as connection:
        connection.execute("UPDATE values_table SET value = 'changed'")
        connection.commit()
    tasks._restore_sqlite_database(backup_path, database_path)
    with sqlite3.connect(database_path) as connection:
        value = connection.execute("SELECT value FROM values_table").fetchone()
    assert value == ("original",)
    deleted = await tasks.delete_database_record(database_id)
    async with session_factory() as session:
        database = await session.get(DatabaseInstance, database_id)
    await engine.dispose()
    assert deleted == {"database_id": database_id, "deleted": True}
    assert database is None
    assert not database_path.exists()


@pytest.mark.asyncio
async def test_database_backup_retention_prunes_old_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Database backup retention removes old rows and matching artifacts."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'db-retention.db'}")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    storage_root = tmp_path / "storage"
    backup_root = storage_root / "backups" / "databases" / "database-id"
    backup_root.mkdir(parents=True)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with session_factory() as session:
        now = datetime.now(UTC)
        for index in range(3):
            artifact = backup_root / f"backup-{index}.sqlite3"
            artifact.write_bytes(f"backup-{index}".encode())
            artifact.with_suffix(artifact.suffix + ".manifest.json").write_text(
                "{}",
                encoding="utf-8",
            )
            session.add(
                Backup(
                    database_id="database-id",
                    backup_type="database",
                    status="ready",
                    path=str(artifact),
                    created_at=now - timedelta(minutes=3 - index),
                )
            )
        await session.commit()
    monkeypatch.setattr(tasks, "SessionFactory", session_factory)
    monkeypatch.setattr(tasks.settings, "storage_root", storage_root)
    await tasks.prune_database_backups("database-id", 1)
    async with session_factory() as session:
        backups = list(await session.scalars(select(Backup)))
    await engine.dispose()
    assert len(backups) == 1
    assert backups[0].path is not None
    assert backups[0].path.endswith("backup-2.sqlite3")
    assert (backup_root / "backup-2.sqlite3").exists()
    assert not (backup_root / "backup-0.sqlite3").exists()
    assert not (backup_root / "backup-1.sqlite3").exists()


@pytest.mark.asyncio
async def test_due_backup_policy_queues_project_and_attached_databases(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Due backup policies enqueue project and ready attached database backups."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'backup-policy.db'}")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    storage_root = tmp_path / "storage"
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with session_factory() as session:
        project = Project(
            name="Backup Project",
            slug="backup-project",
            project_type="backend",
            runtime_type="docker",
        )
        server = DatabaseServer(
            name="SQLite",
            engine="sqlite",
            host="local",
            port=0,
            admin_username="",
            encrypted_admin_password="",
            tls_enabled=False,
        )
        session.add_all([project, server])
        await session.flush()
        project_root = tasks.runtime_root(storage_root, project)
        (project_root / "shared").mkdir(parents=True)
        (project_root / "shared" / "runtime.env").write_text("A=1", encoding="utf-8")
        database = DatabaseInstance(
            server_id=server.id,
            project_id=project.id,
            name="attached_db",
            username="sqlite",
            encrypted_password="",
            engine="sqlite",
            status="ready",
        )
        policy = BackupPolicy(
            project_id=project.id,
            enabled=True,
            schedule="* * * * *",
            retention_count=3,
            include_items=[".env"],
            include_databases=True,
            last_run_at=datetime.now(UTC) - timedelta(hours=1),
        )
        session.add_all([database, policy])
        await session.commit()
    queued: list[tuple[str, str | None, dict[str, Any]]] = []

    def fake_send_task(
        name: str,
        *,
        task_id: str | None = None,
        kwargs: dict[str, Any] | None = None,
    ) -> None:
        """Capture queued Celery tasks."""
        queued.append((name, task_id, kwargs or {}))

    monkeypatch.setattr(tasks, "SessionFactory", session_factory)
    monkeypatch.setattr(tasks.settings, "storage_root", storage_root)
    monkeypatch.setattr(tasks.celery_app, "send_task", fake_send_task)
    result = await tasks.schedule_due_backups()
    async with session_factory() as session:
        backups = list(await session.scalars(select(Backup)))
        jobs = list(await session.scalars(select(Job)))
    await engine.dispose()
    assert result == {"queued": 2}
    assert {item[0] for item in queued} == {"backups.create_project", "databases.export"}
    assert {backup.backup_type for backup in backups} == {"project", "database"}
    assert {job.kind for job in jobs} == {"backup.create_project", "database.export"}
    project_task = next(item for item in queued if item[0] == "backups.create_project")
    database_task = next(item for item in queued if item[0] == "databases.export")
    assert project_task[2]["include_items"] == [".env"]
    assert project_task[2]["retention_count"] == 3
    assert database_task[2]["retention_count"] == 3


@pytest.mark.asyncio
async def test_project_metrics_include_runtime_resources(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Project monitoring samples include agent-reported CPU, RAM and network metrics."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'project-metrics.db'}")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    storage_root = tmp_path / "storage"
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with session_factory() as session:
        project = Project(
            name="Metrics Project",
            slug="metrics-project",
            project_type="backend",
            runtime_type="docker",
            status="stopped",
        )
        session.add(project)
        await session.flush()
        (tasks.runtime_root(storage_root, project) / "current").mkdir(parents=True)
        project_id = project.id
        await session.commit()

    def fake_agent_operation(
        operation: str,
        parameters: dict[str, Any] | None = None,
        *,
        timeout: float = 30,
    ) -> dict[str, Any]:
        """Return deterministic project resource metrics."""
        del timeout
        assert operation == "project_metrics"
        assert parameters == {"project_id": project_id, "runtime_type": "docker"}
        return {
            "resource_status": "running",
            "cpu_percent": 7.5,
            "memory_percent": 12.25,
            "memory_used": 128,
            "memory_total": 1024,
            "network_bytes_sent": 2048,
            "network_bytes_received": 4096,
            "process_count": 1,
        }

    monkeypatch.setattr(tasks, "SessionFactory", session_factory)
    monkeypatch.setattr(tasks.settings, "storage_root", storage_root)
    monkeypatch.setattr(tasks, "agent_operation", fake_agent_operation)

    result = await tasks.store_project_metrics()
    async with session_factory() as session:
        sample = await session.scalar(select(MetricSample))
        updated_project = await session.get(Project, project_id)
    await engine.dispose()

    assert result == {"projects": 1, "health_checked": 0, "health_failed": 0}
    assert sample is not None
    assert sample.project_id == project_id
    assert sample.values["cpu_percent"] == 7.5
    assert sample.values["memory_used"] == 128
    assert sample.values["network_bytes_received"] == 4096
    assert updated_project is not None
    assert updated_project.status == "running"


@pytest.mark.asyncio
async def test_project_metrics_create_resource_policy_violation_alert(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Project monitoring creates one deduplicated alert for policy violations."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'policy-metrics.db'}")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    storage_root = tmp_path / "storage"
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with session_factory() as session:
        project = Project(
            name="Limited Project",
            slug="limited-project",
            project_type="backend",
            runtime_type="docker",
            status="running",
        )
        session.add(project)
        await session.flush()
        session.add(
            ProjectResourcePolicy(
                project_id=project.id,
                enabled=True,
                cpu_cores=0.5,
                memory_mb=64,
                disk_mb=64,
            )
        )
        project_id = project.id
        await session.commit()

    def fake_agent_operation(
        operation: str,
        parameters: dict[str, Any] | None = None,
        *,
        timeout: float = 30,
    ) -> dict[str, Any]:
        """Return metrics above the configured memory limit."""
        del parameters, timeout
        assert operation == "project_metrics"
        return {
            "resource_status": "running",
            "cpu_percent": 25.0,
            "memory_percent": 75.0,
            "memory_used": 128 * 1024 * 1024,
            "memory_total": 256 * 1024 * 1024,
            "network_bytes_sent": 0,
            "network_bytes_received": 0,
            "process_count": 1,
        }

    deliveries: list[tuple[str, str]] = []

    def fake_enqueue(title: str, message: str) -> None:
        """Capture Telegram notification deliveries."""
        deliveries.append((title, message))

    monkeypatch.setattr(tasks, "SessionFactory", session_factory)
    monkeypatch.setattr(tasks.settings, "storage_root", storage_root)
    monkeypatch.setattr(tasks, "agent_operation", fake_agent_operation)
    monkeypatch.setattr(tasks, "enqueue_telegram_alert", fake_enqueue)

    result = await tasks.store_project_metrics()
    async with session_factory() as session:
        sample = await session.scalar(select(MetricSample))
        notification = await session.scalar(select(Notification))
    await engine.dispose()

    assert result == {"projects": 1, "health_checked": 0, "health_failed": 0}
    assert sample is not None
    assert sample.values["resource_policy"]["enabled"] is True
    assert sample.values["resource_violations"] == [
        {
            "resource": "memory",
            "current": 128 * 1024 * 1024,
            "limit": 64 * 1024 * 1024,
            "unit": "bytes",
        }
    ]
    assert notification is not None
    assert notification.notification_type == "project.resource_limit_exceeded"
    assert notification.resource_id == project_id
    assert len(deliveries) == 1
