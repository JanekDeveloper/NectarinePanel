"""Managed database API tests."""

import asyncio
import sqlite3
from pathlib import Path

import pytest
from app.models.entities import DatabaseInstance, DatabaseServer, Job, SystemSetting
from app.services.databases import connection_string, sqlite_database_path
from conftest import TestSession
from fastapi.testclient import TestClient
from sqlalchemy import select


def test_connection_string_escapes_credentials() -> None:
    """Generated DSNs percent-encode user-controlled credentials."""
    dsn = connection_string(
        engine="postgresql",
        host="db.internal",
        port=5432,
        database="app",
        username="user@tenant",
        password="secret:/?#",
    )
    assert dsn == "postgresql://user%40tenant:secret%3A%2F%3F%23@db.internal:5432/app"


def test_create_database_returns_secret_once_and_masks_server(
    client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Provisioning returns generated credentials without leaking admin secrets."""
    from app.api.routes import databases

    monkeypatch.setattr(databases, "enqueue", lambda *args, **kwargs: None)
    server = client.post(
        "/api/v1/databases/servers",
        headers=auth_headers,
        json={
            "name": "Local PostgreSQL",
            "engine": "postgresql",
            "host": "postgres",
            "port": 5432,
            "admin_username": "postgres",
            "admin_password": "admin-secret",
            "tls_enabled": False,
        },
    )
    assert server.status_code == 201
    assert "admin_password" not in server.text
    created = client.post(
        "/api/v1/databases",
        headers=auth_headers,
        json={
            "server_id": server.json()["id"],
            "name": "example_app",
            "username": "example_user",
            "attach_environment": False,
        },
    )
    assert created.status_code == 201
    payload = created.json()
    assert len(payload["password"]) == 32
    assert payload["database"]["status"] == "queued"
    listed = client.get("/api/v1/databases", headers=auth_headers)
    assert payload["password"] not in listed.text
    adminer = client.post(
        f"/api/v1/databases/{payload['database']['id']}/adminer-session",
        headers=auth_headers,
    )
    assert adminer.status_code == 200
    assert adminer.json()["url"].startswith("/adminer/?pgsql=")
    assert "token=" not in adminer.json()["url"]
    session_cookie = adminer.cookies.get("nectarine_adminer_session")
    assert session_cookie
    authorized = client.get(
        "/api/v1/databases/adminer/authorize",
        cookies={"nectarine_adminer_session": session_cookie},
    )
    assert authorized.status_code == 204


def test_delete_database_requires_exact_name_and_queues_worker(
    client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Database deletion requires exact confirmation before changing state."""
    from app.api.routes import databases

    queued: list[tuple[str, str, dict[str, str]]] = []
    monkeypatch.setattr(
        databases,
        "enqueue",
        lambda name, task_id, kwargs: queued.append((name, task_id, kwargs)),
    )
    server = client.post(
        "/api/v1/databases/servers",
        headers=auth_headers,
        json={
            "name": "Delete PostgreSQL",
            "engine": "postgresql",
            "host": "postgres",
            "port": 5432,
            "admin_username": "postgres",
            "admin_password": "admin-secret",
            "tls_enabled": False,
        },
    )
    created = client.post(
        "/api/v1/databases",
        headers=auth_headers,
        json={
            "server_id": server.json()["id"],
            "name": "delete_app",
            "username": "delete_user",
            "attach_environment": False,
        },
    )
    database_id = created.json()["database"]["id"]
    premature = client.request(
        "DELETE",
        f"/api/v1/databases/{database_id}",
        headers=auth_headers,
        json={"confirm_database_name": "delete_app"},
    )
    assert premature.status_code == 409

    async def mark_database_ready() -> None:
        """Complete mocked provisioning before destructive lifecycle checks."""
        async with TestSession() as session:
            database = await session.get(DatabaseInstance, database_id)
            assert database is not None
            database.status = "ready"
            await session.commit()

    asyncio.run(mark_database_ready())
    rejected = client.request(
        "DELETE",
        f"/api/v1/databases/{database_id}",
        headers=auth_headers,
        json={"confirm_database_name": "wrong_name"},
    )
    assert rejected.status_code == 422
    accepted = client.request(
        "DELETE",
        f"/api/v1/databases/{database_id}",
        headers=auth_headers,
        json={"confirm_database_name": "delete_app"},
    )
    assert accepted.status_code == 202
    assert accepted.json()["kind"] == "database.delete"
    assert queued[-1] == (
        "databases.delete",
        accepted.json()["id"],
        {"database_id": database_id},
    )
    listed = client.get("/api/v1/databases", headers=auth_headers)
    record = next(item for item in listed.json() if item["id"] == database_id)
    assert record["status"] == "deleting"


def test_sqlite_database_creation_returns_local_dsn_without_password(
    client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SQLite provisioning uses a panel-managed file and no fake credentials."""
    from app.api.routes import databases

    queued: list[tuple[str, str, dict[str, str]]] = []
    monkeypatch.setattr(
        databases,
        "enqueue",
        lambda name, task_id, kwargs: queued.append((name, task_id, kwargs)),
    )
    server = client.post(
        "/api/v1/databases/servers",
        headers=auth_headers,
        json={
            "name": "Local SQLite",
            "engine": "sqlite",
            "host": "local",
            "port": 0,
            "admin_username": "",
            "admin_password": "",
            "tls_enabled": False,
        },
    )
    assert server.status_code == 201
    created = client.post(
        "/api/v1/databases",
        headers=auth_headers,
        json={
            "server_id": server.json()["id"],
            "name": "local_app",
            "attach_environment": False,
        },
    )
    assert created.status_code == 201
    payload = created.json()
    assert payload["password"] is None
    assert payload["database"]["username"] == "sqlite"
    assert payload["connection_string"].startswith(
        "sqlite+aiosqlite:////tmp/nectarine-panel-tests/databases/sqlite/"
    )
    assert queued == [
        (
            "databases.provision",
            payload["job_id"],
            {"database_id": payload["database"]["id"]},
        )
    ]


def test_sqlite_database_tables_list_ready_tables(
    client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SQLite table listing reads existing managed files without provisioning."""
    from app.api.routes import databases

    monkeypatch.setattr(databases, "enqueue", lambda *args, **kwargs: None)
    server = client.post(
        "/api/v1/databases/servers",
        headers=auth_headers,
        json={
            "name": "Tables SQLite",
            "engine": "sqlite",
            "host": "local",
            "port": 0,
            "admin_username": "",
            "admin_password": "",
            "tls_enabled": False,
        },
    )
    assert server.status_code == 201
    created = client.post(
        "/api/v1/databases",
        headers=auth_headers,
        json={
            "server_id": server.json()["id"],
            "name": "tables_app",
            "attach_environment": False,
        },
    )
    assert created.status_code == 201
    database_id = created.json()["database"]["id"]
    path = sqlite_database_path(Path("/tmp/nectarine-panel-tests"), database_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE users (id INTEGER PRIMARY KEY)")
        connection.execute("CREATE TABLE deployments (id INTEGER PRIMARY KEY)")

    async def mark_database_ready() -> None:
        """Make mocked provisioning visible to the table-listing endpoint."""
        async with TestSession() as session:
            database = await session.get(DatabaseInstance, database_id)
            assert database is not None
            database.status = "ready"
            await session.commit()

    asyncio.run(mark_database_ready())
    response = client.get(
        f"/api/v1/databases/{database_id}/tables",
        headers=auth_headers,
    )
    assert response.status_code == 200
    assert response.json() == [
        {"schema": None, "name": "deployments", "table_type": "table"},
        {"schema": None, "name": "users", "table_type": "table"},
    ]


def test_database_server_status_masks_credentials(
    client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Server checks decrypt credentials internally and expose bounded metadata."""
    from app.api.routes import databases

    captured: dict[str, str] = {}

    async def fake_inspect(
        server: DatabaseServer,
        password: str,
        storage_root: object,
    ) -> dict[str, object]:
        """Capture the decrypted credential without returning it."""
        del storage_root
        captured["engine"] = server.engine
        captured["password"] = password
        return {
            "engine": server.engine,
            "status": "ready",
            "version": "8.0",
            "details": {"connected_clients": 2},
        }

    monkeypatch.setattr(databases, "inspect_database_server", fake_inspect)
    server = client.post(
        "/api/v1/databases/servers",
        headers=auth_headers,
        json={
            "name": "Local Valkey",
            "engine": "valkey",
            "host": "valkey",
            "port": 6379,
            "admin_username": "default",
            "admin_password": "valkey-secret",
            "tls_enabled": False,
        },
    )
    response = client.get(
        f"/api/v1/databases/servers/{server.json()['id']}/status",
        headers=auth_headers,
    )
    assert response.status_code == 200
    assert response.json()["details"] == {"connected_clients": 2}
    assert "valkey-secret" not in response.text
    assert captured == {"engine": "valkey", "password": "valkey-secret"}


def test_local_database_status_falls_back_to_agent(
    client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Local imported database checks use the agent when password auth fails."""
    from app.api.routes import databases
    from app.services.agent import AgentClient

    async def fail_inspect(*args: object, **kwargs: object) -> dict[str, object]:
        """Simulate local socket-only authentication."""
        del args, kwargs
        raise RuntimeError("password auth failed")

    async def fake_execute(
        self: AgentClient,
        operation: str,
        parameters: dict[str, object] | None = None,
        *,
        request_timeout: float = 30,
    ) -> dict[str, object]:
        """Return bounded local database metadata."""
        del self, request_timeout
        assert operation == "database_server_status"
        assert parameters == {"engine": "postgresql"}
        return {
            "engine": "postgresql",
            "status": "ready",
            "version": "16",
            "details": {"database_count": 3},
        }

    monkeypatch.setattr(databases, "inspect_database_server", fail_inspect)
    monkeypatch.setattr(AgentClient, "execute", fake_execute)
    server = client.post(
        "/api/v1/databases/servers",
        headers=auth_headers,
        json={
            "name": "Local PostgreSQL",
            "engine": "postgresql",
            "host": "127.0.0.1",
            "port": 5432,
            "admin_username": "postgres",
            "admin_password": "placeholder",
            "tls_enabled": False,
        },
    )
    response = client.get(
        f"/api/v1/databases/servers/{server.json()['id']}/status",
        headers=auth_headers,
    )
    assert response.status_code == 200
    assert response.json()["details"] == {"database_count": 3}


def test_telegram_database_restore_upload_queues_import(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The internal bot API stores an owner dump upload and queues import."""
    from app.api.routes import telegram

    queued: list[tuple[str, str, dict[str, str]]] = []
    monkeypatch.setattr(
        telegram,
        "enqueue",
        lambda name, task_id, kwargs: queued.append((name, task_id, kwargs)),
    )

    async def seed_database() -> str:
        """Create a ready PostgreSQL database and Telegram owner setting."""
        async with TestSession() as session:
            server = DatabaseServer(
                name="Local PostgreSQL",
                engine="postgresql",
                host="postgres",
                port=5432,
                admin_username="postgres",
                encrypted_admin_password="secret",
                tls_enabled=False,
            )
            session.add(server)
            await session.flush()
            database = DatabaseInstance(
                server_id=server.id,
                name="app",
                username="app",
                encrypted_password="secret",
                engine="postgresql",
                status="ready",
            )
            session.add(database)
            session.add(
                SystemSetting(
                    key="telegram.owner_id",
                    value="12345",
                    is_secret=False,
                )
            )
            await session.commit()
            return database.id

    database_id = asyncio.run(seed_database())
    response = client.post(
        f"/api/v1/telegram/databases/{database_id}/restore",
        headers={"X-Telegram-Service-Token": "development-telegram-service-token"},
        data={"telegram_user_id": "12345", "username": "owner"},
        files={"file": ("dump.dump", b"PGDMP", "application/octet-stream")},
    )
    assert response.status_code == 200
    job_id = response.json()["job_id"]
    assert queued == [
        (
            "databases.import",
            job_id,
            {
                "database_id": database_id,
                "dump_path": f"/tmp/nectarine-panel-tests/backups/incoming/{job_id}.dump",
            },
        )
    ]

    async def load_job() -> Job | None:
        """Load the queued import job."""
        async with TestSession() as session:
            return await session.scalar(select(Job).where(Job.id == job_id))

    job = asyncio.run(load_job())
    assert job is not None
    assert job.kind == "database.import"
