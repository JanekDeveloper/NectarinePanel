"""Managed database validation and connection-string helpers."""

import secrets
import string
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import quote
from uuid import UUID

import aiosqlite
import asyncmy
import asyncpg
from redis.asyncio import Redis


class DatabaseServerSource(Protocol):
    """Minimal database server fields required for status inspection."""

    engine: str
    host: str
    port: int
    admin_username: str
    tls_enabled: bool


class DatabaseInstanceSource(Protocol):
    """Minimal database instance fields required for table inspection."""

    id: str
    engine: str
    name: str
    username: str


def generate_database_password(length: int = 32) -> str:
    """Generate a high-entropy password accepted by common database engines."""
    if length < 24:
        raise ValueError("Database passwords must contain at least 24 characters")
    alphabet = string.ascii_letters + string.digits + "-_"
    return "".join(secrets.choice(alphabet) for _ in range(length))


def connection_string(
    *,
    engine: str,
    host: str,
    port: int,
    database: str,
    username: str,
    password: str,
) -> str:
    """Build an RFC-compatible connection string with escaped credentials."""
    scheme = "mysql" if engine == "mariadb" else engine
    return (
        f"{scheme}://{quote(username, safe='')}:{quote(password, safe='')}"
        f"@{host}:{port}/{quote(database, safe='')}"
    )


def sqlite_database_path(storage_root: Path, database_id: str) -> Path:
    """Return a stable SQLite file path below panel-managed storage."""
    try:
        normalized = str(UUID(database_id))
    except ValueError as exc:
        raise ValueError("Invalid SQLite database ID") from exc
    root = (storage_root / "databases" / "sqlite").resolve()
    candidate = (root / f"{normalized}.sqlite3").resolve()
    if root not in candidate.parents:
        raise ValueError("SQLite path escapes managed storage")
    return candidate


def sqlite_connection_string(path: Path) -> str:
    """Build an absolute SQLAlchemy-compatible SQLite connection string."""
    return f"sqlite+aiosqlite:///{path.resolve()}"


async def inspect_database_server(
    server: DatabaseServerSource,
    admin_password: str,
    storage_root: Path,
) -> dict[str, Any]:
    """Return bounded connectivity and version details for one database server."""
    if server.engine == "postgresql":
        connection = await asyncpg.connect(
            host=server.host,
            port=server.port,
            user=server.admin_username,
            password=admin_password,
            database="postgres",
            ssl="require" if server.tls_enabled else False,
            timeout=5,
        )
        try:
            version = str(await connection.fetchval("SHOW server_version"))
            database_count = int(
                await connection.fetchval(
                    "SELECT count(*) FROM pg_database WHERE datistemplate = false"
                )
            )
        finally:
            await connection.close()
        return {
            "engine": server.engine,
            "status": "ready",
            "version": version,
            "details": {"database_count": database_count},
        }
    if server.engine in {"mysql", "mariadb"}:
        connection = await asyncmy.connect(
            host=server.host,
            port=server.port,
            user=server.admin_username,
            password=admin_password,
            ssl=server.tls_enabled,
            autocommit=True,
            connect_timeout=5,
        )
        try:
            async with connection.cursor() as cursor:
                await cursor.execute("SELECT VERSION()")
                version_row = await cursor.fetchone()
                await cursor.execute("SELECT COUNT(*) FROM information_schema.schemata")
                count_row = await cursor.fetchone()
        finally:
            connection.close()
        return {
            "engine": server.engine,
            "status": "ready",
            "version": str(version_row[0]) if version_row else None,
            "details": {"database_count": int(count_row[0]) if count_row else 0},
        }
    if server.engine in {"redis", "valkey"}:
        client: Redis = Redis(
            host=server.host,
            port=server.port,
            username=server.admin_username or None,
            password=admin_password or None,
            ssl=server.tls_enabled,
            socket_connect_timeout=5,
            socket_timeout=5,
            decode_responses=True,
        )
        try:
            await client.ping()  # type: ignore[misc]  # Redis overload includes sync.
            server_info = await client.info("server")
            clients_info = await client.info("clients")
            memory_info = await client.info("memory")
        finally:
            await client.aclose()
        version = server_info.get("valkey_version") or server_info.get("redis_version")
        return {
            "engine": server.engine,
            "status": "ready",
            "version": str(version) if version is not None else None,
            "details": {
                "uptime_seconds": int(server_info.get("uptime_in_seconds", 0)),
                "connected_clients": int(clients_info.get("connected_clients", 0)),
                "used_memory_bytes": int(memory_info.get("used_memory", 0)),
            },
        }
    if server.engine == "sqlite":
        directory = (storage_root / "databases" / "sqlite").resolve()
        return {
            "engine": server.engine,
            "status": "ready",
            "version": None,
            "details": {
                "storage_path": str(directory),
                "database_count": len(list(directory.glob("*.sqlite3")))
                if directory.is_dir()
                else 0,
            },
        }
    raise ValueError("Unsupported database engine")


async def list_database_tables(
    instance: DatabaseInstanceSource,
    server: DatabaseServerSource,
    password: str,
    storage_root: Path,
    *,
    limit: int = 500,
) -> list[dict[str, str | None]]:
    """Return a bounded list of relational tables for a managed database."""
    if limit < 1 or limit > 1000:
        raise ValueError("Table list limit must be between 1 and 1000")
    if instance.engine == "postgresql":
        connection = await asyncpg.connect(
            host=server.host,
            port=server.port,
            user=instance.username,
            password=password,
            database=instance.name,
            ssl="require" if server.tls_enabled else False,
            timeout=5,
        )
        try:
            rows = await connection.fetch(
                """
                SELECT table_schema, table_name
                FROM information_schema.tables
                WHERE table_type = 'BASE TABLE'
                  AND table_schema NOT IN ('pg_catalog', 'information_schema')
                ORDER BY table_schema, table_name
                LIMIT $1
                """,
                limit,
            )
        finally:
            await connection.close()
        return [
            {
                "schema": str(row["table_schema"]),
                "name": str(row["table_name"]),
                "table_type": "table",
            }
            for row in rows
        ]
    if instance.engine in {"mysql", "mariadb"}:
        connection = await asyncmy.connect(
            host=server.host,
            port=server.port,
            user=instance.username,
            password=password,
            db=instance.name,
            ssl=server.tls_enabled,
            autocommit=True,
            connect_timeout=5,
        )
        try:
            async with connection.cursor() as cursor:
                await cursor.execute(
                    """
                    SELECT table_name
                    FROM information_schema.tables
                    WHERE table_schema = %s AND table_type = 'BASE TABLE'
                    ORDER BY table_name
                    LIMIT %s
                    """,
                    (instance.name, limit),
                )
                rows = await cursor.fetchall()
        finally:
            connection.close()
        return [
            {"schema": instance.name, "name": str(row[0]), "table_type": "table"}
            for row in rows
        ]
    if instance.engine == "sqlite":
        path = sqlite_database_path(storage_root, instance.id)
        if not path.is_file():
            return []
        async with aiosqlite.connect(f"file:{path}?mode=ro", uri=True) as connection:
            sqlite_cursor = await connection.execute(
                """
                SELECT name
                FROM sqlite_master
                WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
                ORDER BY name
                LIMIT ?
                """,
                (limit,),
            )
            rows = await sqlite_cursor.fetchall()
            await sqlite_cursor.close()
        return [{"schema": None, "name": str(row[0]), "table_type": "table"} for row in rows]
    raise ValueError("Unsupported database engine")
