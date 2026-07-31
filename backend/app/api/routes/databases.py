"""Managed database server and instance endpoints."""

from pathlib import Path
from urllib.parse import urlencode

import anyio
import jwt
from fastapi import APIRouter, Cookie, Form, HTTPException, Response, UploadFile, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.api.dependencies import AppSettings, CurrentUser, DbSession
from app.core.security import SecretCipher, create_scoped_token, decode_scoped_token
from app.models.entities import (
    Backup,
    DatabaseInstance,
    DatabaseServer,
    DatabaseUser,
    EnvironmentVariable,
    Job,
    Project,
)
from app.schemas.backup import BackupJobResponse, BackupResponse
from app.schemas.database import (
    DatabaseDeleteRequest,
    DatabaseInstanceCreate,
    DatabaseInstanceCreated,
    DatabaseInstanceResponse,
    DatabaseServerCreate,
    DatabaseServerResponse,
    DatabaseServerStatus,
    DatabaseTableResponse,
)
from app.schemas.jobs import JobResponse
from app.services.agent import AgentClient
from app.services.audit import write_audit_log
from app.services.databases import (
    connection_string,
    generate_database_password,
    inspect_database_server,
    list_database_tables,
    sqlite_connection_string,
    sqlite_database_path,
)
from app.services.queue import enqueue

router = APIRouter(prefix="/databases", tags=["databases"])
ADMINER_COOKIE = "nectarine_adminer_session"


def _is_local_database_server(server: DatabaseServer) -> bool:
    """Return whether the database server is the local host."""
    return server.host in {"127.0.0.1", "localhost", "::1", "local"}


async def _inspect_local_database_server(
    server: DatabaseServer,
    settings: AppSettings,
) -> dict[str, object]:
    """Inspect a local socket database server through the system agent."""
    if server.engine not in {"postgresql", "mysql", "mariadb"}:
        raise ValueError("Unsupported local database engine")
    return await AgentClient(settings).execute(
        "database_server_status",
        {"engine": server.engine},
        request_timeout=45,
    )


async def _list_local_database_tables(
    instance: DatabaseInstance,
    settings: AppSettings,
) -> list[dict[str, str | None]]:
    """List local socket database tables through the system agent."""
    result = await AgentClient(settings).execute(
        "database_tables",
        {"engine": instance.engine, "database": instance.name, "limit": 500},
        request_timeout=45,
    )
    tables = result.get("tables", [])
    if not isinstance(tables, list):
        raise ValueError("Invalid database table response")
    return tables


def _cipher_or_409(settings: AppSettings) -> SecretCipher:
    """Require field encryption outside local development."""
    if (
        settings.environment not in {"development", "test"}
        and not settings.field_encryption_key
    ):
        raise HTTPException(
            status_code=409,
            detail="FIELD_ENCRYPTION_KEY must be configured before storing credentials",
        )
    return SecretCipher(settings.field_encryption_key)


async def _require_stopped_sqlite_project(
    instance: DatabaseInstance,
    session: DbSession,
) -> None:
    """Reject SQLite mutation while an attached project may hold open handles."""
    if instance.engine != "sqlite" or not instance.project_id:
        return
    project = await session.get(Project, instance.project_id)
    if project is not None and project.status in {"running", "deploying"}:
        raise HTTPException(
            status_code=409,
            detail="Stop the attached project before modifying its SQLite database",
        )


@router.get("/servers", response_model=list[DatabaseServerResponse])
async def list_database_servers(user: CurrentUser, session: DbSession) -> list[DatabaseServer]:
    """List configured database administration connections."""
    del user
    result = await session.scalars(select(DatabaseServer).order_by(DatabaseServer.name))
    return list(result)


@router.post(
    "/servers",
    response_model=DatabaseServerResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_database_server(
    data: DatabaseServerCreate,
    user: CurrentUser,
    session: DbSession,
    settings: AppSettings,
) -> DatabaseServer:
    """Store an encrypted database administration connection."""
    cipher = _cipher_or_409(settings)
    server = DatabaseServer(
        name=data.name,
        engine=data.engine,
        host=data.host,
        port=data.port,
        admin_username=data.admin_username,
        encrypted_admin_password=cipher.encrypt(data.admin_password),
        tls_enabled=data.tls_enabled,
    )
    session.add(server)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=409, detail="Database server name already exists"
        ) from exc
    await write_audit_log(
        session,
        action="database.server_add",
        actor_id=user.id,
        resource_type="database_server",
        resource_id=server.id,
        details={"name": server.name, "engine": server.engine, "host": server.host},
    )
    await session.commit()
    await session.refresh(server)
    return server


@router.get("/servers/{server_id}/status", response_model=DatabaseServerStatus)
async def database_server_status(
    server_id: str,
    user: CurrentUser,
    session: DbSession,
    settings: AppSettings,
) -> dict[str, object]:
    """Check database connectivity and return bounded engine information."""
    del user
    server = await session.get(DatabaseServer, server_id)
    if server is None:
        raise HTTPException(status_code=404, detail="Database server not found")
    password = _cipher_or_409(settings).decrypt(server.encrypted_admin_password)
    try:
        return await inspect_database_server(server, password, settings.storage_root)
    except Exception as exc:
        if _is_local_database_server(server):
            try:
                return await _inspect_local_database_server(server, settings)
            except Exception as fallback_exc:
                raise HTTPException(
                    status_code=502,
                    detail="Database server check failed",
                ) from fallback_exc
        raise HTTPException(status_code=502, detail="Database server check failed") from exc


@router.get("", response_model=list[DatabaseInstanceResponse])
async def list_databases(user: CurrentUser, session: DbSession) -> list[DatabaseInstance]:
    """List managed database instances."""
    del user
    result = await session.scalars(
        select(DatabaseInstance).order_by(DatabaseInstance.created_at.desc())
    )
    return list(result)


@router.post(
    "",
    response_model=DatabaseInstanceCreated,
    status_code=status.HTTP_201_CREATED,
)
async def create_database(
    data: DatabaseInstanceCreate,
    user: CurrentUser,
    session: DbSession,
    settings: AppSettings,
) -> DatabaseInstanceCreated:
    """Create database metadata, attach env, and queue provisioning."""
    cipher = _cipher_or_409(settings)
    server = await session.get(DatabaseServer, data.server_id)
    if server is None:
        raise HTTPException(status_code=404, detail="Database server not found")
    if server.engine not in {"postgresql", "mysql", "mariadb", "sqlite"}:
        raise HTTPException(
            status_code=409,
            detail="This engine does not support database/user provisioning",
        )
    if server.engine != "sqlite" and not data.username:
        raise HTTPException(status_code=422, detail="Database username is required")
    project = None
    if data.project_id:
        project = await session.get(Project, data.project_id)
        if project is None:
            raise HTTPException(status_code=404, detail="Project not found")
    password = None if server.engine == "sqlite" else generate_database_password()
    username = "sqlite" if server.engine == "sqlite" else data.username
    instance = DatabaseInstance(
        server_id=server.id,
        project_id=project.id if project else None,
        name=data.name,
        username=username,
        encrypted_password=cipher.encrypt(password or ""),
        engine=server.engine,
        status="queued",
    )
    session.add(instance)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=409, detail="Database already exists on this server"
        ) from exc
    job = Job(
        kind="database.provision",
        payload={"database_id": instance.id, "engine": server.engine},
    )
    session.add(job)
    if server.engine != "sqlite":
        session.add(
            DatabaseUser(
                database_id=instance.id,
                username=instance.username,
                encrypted_password=instance.encrypted_password,
                privileges=["ALL"],
            )
        )
    dsn = (
        sqlite_connection_string(sqlite_database_path(settings.storage_root, instance.id))
        if server.engine == "sqlite"
        else connection_string(
            engine=server.engine,
            host=server.host,
            port=server.port,
            database=instance.name,
            username=instance.username,
            password=password or "",
        )
    )
    if project is not None and data.attach_environment:
        variable = await session.scalar(
            select(EnvironmentVariable).where(
                EnvironmentVariable.project_id == project.id,
                EnvironmentVariable.key == "DATABASE_URL",
            )
        )
        if variable is None:
            variable = EnvironmentVariable(
                project_id=project.id,
                key="DATABASE_URL",
                encrypted_value=cipher.encrypt(dsn),
                is_secret=True,
            )
            session.add(variable)
        else:
            variable.encrypted_value = cipher.encrypt(dsn)
            variable.is_secret = True
    await write_audit_log(
        session,
        action="database.create",
        actor_id=user.id,
        resource_type="database",
        resource_id=instance.id,
        details={"name": instance.name, "engine": instance.engine},
    )
    await session.commit()
    enqueue(
        "databases.provision",
        task_id=job.id,
        kwargs={"database_id": instance.id},
    )
    return DatabaseInstanceCreated(
        database=DatabaseInstanceResponse.model_validate(instance),
        password=password,
        connection_string=dsn,
        job_id=job.id,
    )


@router.get("/{database_id}/tables", response_model=list[DatabaseTableResponse])
async def database_tables(
    database_id: str,
    user: CurrentUser,
    session: DbSession,
    settings: AppSettings,
) -> list[dict[str, str | None]]:
    """List relational tables for a ready managed database."""
    del user
    instance = await session.get(DatabaseInstance, database_id)
    if instance is None:
        raise HTTPException(status_code=404, detail="Database not found")
    if instance.status != "ready":
        raise HTTPException(status_code=409, detail="Database is not ready")
    if instance.engine not in {"postgresql", "mysql", "mariadb", "sqlite"}:
        raise HTTPException(
            status_code=409,
            detail="This engine does not expose relational tables",
        )
    server = await session.get(DatabaseServer, instance.server_id)
    if server is None:
        raise HTTPException(status_code=409, detail="Database server is unavailable")
    password = _cipher_or_409(settings).decrypt(instance.encrypted_password)
    try:
        return await list_database_tables(instance, server, password, settings.storage_root)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        if _is_local_database_server(server):
            try:
                return await _list_local_database_tables(instance, settings)
            except Exception as fallback_exc:
                raise HTTPException(
                    status_code=502,
                    detail="Database table listing failed",
                ) from fallback_exc
        raise HTTPException(status_code=502, detail="Database table listing failed") from exc


@router.delete(
    "/{database_id}",
    response_model=JobResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def delete_database(
    database_id: str,
    data: DatabaseDeleteRequest,
    user: CurrentUser,
    session: DbSession,
) -> Job:
    """Queue destructive database and role deletion after exact confirmation."""
    instance = await session.get(DatabaseInstance, database_id)
    if instance is None:
        raise HTTPException(status_code=404, detail="Database not found")
    if data.confirm_database_name != instance.name:
        raise HTTPException(status_code=422, detail="Database name confirmation mismatch")
    if instance.engine not in {"postgresql", "mysql", "mariadb", "sqlite"}:
        raise HTTPException(status_code=409, detail="Database engine cannot be deleted")
    if instance.status == "deleting":
        raise HTTPException(status_code=409, detail="Database deletion is already queued")
    if instance.status not in {"ready", "failed", "delete_failed"}:
        raise HTTPException(
            status_code=409,
            detail="Database provisioning must finish before deletion",
        )
    await _require_stopped_sqlite_project(instance, session)
    job = Job(
        kind="database.delete",
        payload={"database_id": instance.id, "engine": instance.engine},
    )
    session.add(job)
    instance.status = "deleting"
    await write_audit_log(
        session,
        action="database.delete",
        actor_id=user.id,
        resource_type="database",
        resource_id=instance.id,
        details={"name": instance.name, "engine": instance.engine},
    )
    await session.commit()
    enqueue(
        "databases.delete",
        task_id=job.id,
        kwargs={"database_id": instance.id},
    )
    return job


@router.post(
    "/{database_id}/export",
    response_model=BackupJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def export_database(
    database_id: str,
    user: CurrentUser,
    session: DbSession,
) -> BackupJobResponse:
    """Queue a managed database dump and backup manifest."""
    instance = await session.get(DatabaseInstance, database_id)
    if instance is None:
        raise HTTPException(status_code=404, detail="Database not found")
    if instance.status != "ready":
        raise HTTPException(status_code=409, detail="Database is not ready")
    backup = Backup(
        database_id=instance.id,
        backup_type="database",
        status="queued",
    )
    session.add(backup)
    await session.flush()
    job = Job(
        kind="database.export",
        payload={"database_id": instance.id, "backup_id": backup.id},
    )
    session.add(job)
    await write_audit_log(
        session,
        action="database.export",
        actor_id=user.id,
        resource_type="database",
        resource_id=instance.id,
    )
    await session.commit()
    enqueue(
        "databases.export",
        task_id=job.id,
        kwargs={"database_id": instance.id, "backup_id": backup.id},
    )
    return BackupJobResponse(
        backup=BackupResponse.model_validate(backup),
        job_id=job.id,
    )


@router.post("/{database_id}/adminer-session")
async def create_adminer_session(
    database_id: str,
    response: Response,
    user: CurrentUser,
    session: DbSession,
    settings: AppSettings,
) -> dict[str, str]:
    """Create a short-lived browser handoff into protected Adminer."""
    instance = await session.get(DatabaseInstance, database_id)
    if instance is None:
        raise HTTPException(status_code=404, detail="Database not found")
    if instance.engine not in {"postgresql", "mysql", "mariadb", "sqlite"}:
        raise HTTPException(status_code=409, detail="Adminer does not support this database")
    server = await session.get(DatabaseServer, instance.server_id)
    if server is None:
        raise HTTPException(status_code=409, detail="Database server is unavailable")
    token = create_scoped_token(
        user.id,
        "adminer",
        settings,
        claims={"database_id": instance.id},
    )
    await write_audit_log(
        session,
        action="database.adminer_open",
        actor_id=user.id,
        resource_type="database",
        resource_id=instance.id,
    )
    await session.commit()
    response.set_cookie(
        ADMINER_COOKIE,
        token,
        max_age=600,
        httponly=True,
        secure=settings.public_base_url.startswith("https://"),
        samesite="strict",
        path="/adminer",
    )
    if instance.engine == "sqlite":
        driver = "sqlite"
        query_values = {
            driver: str(sqlite_database_path(settings.storage_root, instance.id)),
            "db": instance.name,
        }
    else:
        driver = "pgsql" if instance.engine == "postgresql" else "mysql"
        query_values = {
            driver: f"{server.host}:{server.port}",
            "username": instance.username,
            "db": instance.name,
        }
    query = urlencode(query_values)
    return {"url": f"/adminer/?{query}"}


@router.get("/adminer/authorize", include_in_schema=False)
async def authorize_adminer(
    settings: AppSettings,
    session_token: str | None = Cookie(default=None, alias=ADMINER_COOKIE),
) -> Response:
    """Authorize an Nginx subrequest using the scoped Adminer cookie."""
    if not session_token:
        raise HTTPException(status_code=401, detail="Adminer authentication required")
    try:
        decode_scoped_token(session_token, "adminer", settings)
    except jwt.InvalidTokenError as exc:
        raise HTTPException(status_code=401, detail="Adminer session expired") from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/{database_id}/import",
    response_model=dict[str, str],
    status_code=status.HTTP_202_ACCEPTED,
)
async def import_database(
    database_id: str,
    file: UploadFile,
    user: CurrentUser,
    session: DbSession,
    settings: AppSettings,
    confirm_database_name: str = Form(),
) -> dict[str, str]:
    """Upload a dump and queue destructive database restore."""
    instance = await session.get(DatabaseInstance, database_id)
    if instance is None:
        raise HTTPException(status_code=404, detail="Database not found")
    if confirm_database_name != instance.name:
        raise HTTPException(status_code=422, detail="Database name confirmation mismatch")
    await _require_stopped_sqlite_project(instance, session)
    filename = file.filename or ""
    if instance.engine == "postgresql":
        expected_suffixes = {".dump"}
    elif instance.engine in {"mysql", "mariadb"}:
        expected_suffixes = {".sql"}
    elif instance.engine == "sqlite":
        expected_suffixes = {".db", ".sqlite", ".sqlite3"}
    else:
        raise HTTPException(status_code=409, detail="Database engine cannot be imported")
    if Path(filename).suffix.lower() not in expected_suffixes:
        raise HTTPException(status_code=415, detail="Dump format does not match engine")
    incoming = settings.storage_root / "backups" / "incoming"
    incoming.mkdir(parents=True, exist_ok=True)
    job = Job(
        kind="database.import",
        payload={"database_id": instance.id},
    )
    session.add(job)
    await session.flush()
    target = incoming / f"{job.id}{Path(filename).suffix.lower()}"
    total = 0
    try:
        async with await anyio.open_file(target, "xb") as handle:
            while chunk := await file.read(1024 * 1024):
                total += len(chunk)
                if total > settings.max_upload_bytes:
                    raise HTTPException(status_code=413, detail="Dump exceeds size limit")
                await handle.write(chunk)
    except Exception:
        target.unlink(missing_ok=True)
        await session.rollback()
        raise
    finally:
        await file.close()
    await write_audit_log(
        session,
        action="database.import",
        actor_id=user.id,
        resource_type="database",
        resource_id=instance.id,
        details={"size_bytes": total},
    )
    await session.commit()
    enqueue(
        "databases.import",
        task_id=job.id,
        kwargs={"database_id": instance.id, "dump_path": str(target)},
    )
    return {"job_id": job.id}
