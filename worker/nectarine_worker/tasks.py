"""Background deployment and backup tasks."""

import asyncio
import contextlib
import json
import logging
import re
import shutil
import sqlite3
import tempfile
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import aiosqlite
import asyncmy
import asyncpg
import httpx
from app.core.config import get_settings
from app.core.security import SecretCipher
from app.models.entities import (
    MINECRAFT_TYPES,
    Backup,
    BackupPolicy,
    CronJob,
    DatabaseInstance,
    DatabaseServer,
    DatabaseUser,
    Domain,
    EnvironmentVariable,
    Job,
    MetricSample,
    Notification,
    Project,
    ProjectResourcePolicy,
    ProjectRuntime,
    ProjectSource,
    SslCertificate,
)
from app.services.databases import (
    connection_string,
    sqlite_connection_string,
    sqlite_database_path,
)
from app.services.minecraft import minecraft_parameters
from app.services.minecraft_operations import reserve_minecraft_job
from app.services.paths import runtime_root
from app.services.system_settings import alert_thresholds
from app.version import APP_VERSION
from croniter import croniter
from sqlalchemy import delete, select
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from nectarine_worker.celery_app import celery_app
from nectarine_worker.minecraft import persist_phase, reserved_operation, stop_parameters
from nectarine_worker.notifications import create_owner_alert, enqueue_telegram_alert
from nectarine_worker.runner import (
    BACKUP_FORMAT_VERSION,
    RESTORE_COMPATIBILITY,
    create_backup,
    create_tar_gz_safely,
    encrypt_backup,
    extract_tar_safely,
    extract_zip_safely,
    restore_backup,
    run,
    run_project_command,
    sha256_file,
    validate_backup_archive,
)

settings = get_settings()
worker_engine = create_async_engine(
    settings.database_url,
    poolclass=NullPool,
    echo=settings.environment == "development",
)
SessionFactory = async_sessionmaker(worker_engine, expire_on_commit=False)
logger = logging.getLogger(__name__)
DATABASE_IDENTIFIER = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]{0,62}$")
FULL_BACKUP_ITEMS = {
    "panel_db",
    "panel_config",
    "nginx_configs",
    "project_files",
    "minecraft_files",
    "sqlite_databases",
    "state",
}


class AgentOperationError(RuntimeError):
    """Raised when the local system agent rejects a worker operation."""


def agent_operation(
    operation: str,
    parameters: dict[str, Any] | None = None,
    *,
    timeout: float = 30,
) -> dict[str, Any]:
    """Execute one allowlisted local agent operation."""
    response = httpx.post(
        f"{settings.agent_url.rstrip('/')}/v1/operations",
        headers={"Authorization": f"Bearer {settings.agent_token}"},
        json={"operation": operation, "parameters": parameters or {}},
        timeout=timeout,
    )
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        detail: object = None
        with contextlib.suppress(ValueError):
            payload = response.json()
            if isinstance(payload, dict):
                detail = payload.get("detail")
        message = (
            str(detail)
            if isinstance(detail, str) and detail.strip()
            else f"System agent rejected operation {operation}"
        )
        raise AgentOperationError(message) from exc
    payload = cast(dict[str, Any], response.json())
    return cast(dict[str, Any], payload["result"])


def _database_identifier(value: str) -> str:
    """Validate a database identifier before quoting it."""
    if not DATABASE_IDENTIFIER.fullmatch(value):
        raise ValueError("Invalid database identifier")
    return value


def _worker_project_path(project_root: str, relative: str) -> tuple[Path, Path]:
    """Resolve a worker path below the configured project storage root."""
    allowed = (settings.storage_root / "projects").resolve()
    root = Path(project_root).resolve()
    if root == allowed or allowed not in root.parents:
        raise ValueError("Project root escapes configured storage")
    candidate = (root / relative.lstrip("/")).resolve()
    if candidate != root and root not in candidate.parents:
        raise ValueError("File path escapes project root")
    return root, candidate


def _directory_size(root: Path, *, max_entries: int = 50000) -> int:
    """Calculate a bounded recursive directory size without following symlinks."""
    try:
        exists = root.exists()
    except OSError:
        return 0
    if not exists:
        return 0
    total = 0
    entries = 0
    try:
        for path in root.rglob("*"):
            entries += 1
            if entries > max_entries:
                break
            if path.is_symlink() or not path.is_file():
                continue
            total += path.stat().st_size
    except OSError:
        return total
    return total


def _memory_limit_megabytes(value: str) -> int:
    """Convert a Minecraft memory value to MiB."""
    match = re.fullmatch(r"([1-9][0-9]{0,5})([MGmg])", value)
    if match is None:
        raise ValueError("Invalid Minecraft memory value")
    amount = int(match.group(1))
    return amount * 1024 if match.group(2).lower() == "g" else amount


def _policy_response(policy: ProjectResourcePolicy | None) -> dict[str, Any]:
    """Serialize a resource policy for metrics payloads."""
    if policy is None:
        return {
            "enabled": False,
            "cpu_cores": None,
            "memory_mb": None,
            "disk_mb": None,
        }
    return {
        "enabled": policy.enabled,
        "cpu_cores": policy.cpu_cores,
        "memory_mb": policy.memory_mb,
        "disk_mb": policy.disk_mb,
    }


def _policy_deploy_parameters(policy: dict[str, Any], runtime_type: str) -> dict[str, Any]:
    """Build runtime enforcement parameters for supported runtimes."""
    if not policy.get("enabled"):
        return {}
    if runtime_type == "docker_compose":
        return {}
    parameters: dict[str, Any] = {}
    if policy.get("cpu_cores") is not None:
        parameters["cpu_cores"] = float(policy["cpu_cores"])
    if policy.get("memory_mb") is not None:
        parameters["memory_mb"] = int(policy["memory_mb"])
    return parameters


def _resource_violations(
    values: dict[str, Any],
    policy: ProjectResourcePolicy | None,
) -> list[dict[str, Any]]:
    """Return resource limit violations for one collected project sample."""
    if policy is None or not policy.enabled:
        return []
    violations: list[dict[str, Any]] = []
    if policy.cpu_cores is not None:
        current = float(values.get("cpu_percent") or 0.0)
        limit = float(policy.cpu_cores) * 100.0
        if current > limit:
            violations.append(
                {
                    "resource": "cpu",
                    "current": round(current, 2),
                    "limit": round(limit, 2),
                    "unit": "percent",
                }
            )
    if policy.memory_mb is not None:
        current = int(values.get("memory_used") or 0)
        limit = int(policy.memory_mb) * 1024 * 1024
        if current > limit:
            violations.append(
                {
                    "resource": "memory",
                    "current": current,
                    "limit": limit,
                    "unit": "bytes",
                }
            )
    if policy.disk_mb is not None:
        current = int(values.get("disk_bytes") or 0)
        limit = int(policy.disk_mb) * 1024 * 1024
        if current > limit:
            violations.append(
                {
                    "resource": "disk",
                    "current": current,
                    "limit": limit,
                    "unit": "bytes",
                }
            )
    return violations


def _project_metric_parameters(project: Project, root: Path) -> dict[str, str]:
    """Build validated agent metadata needed for one project metric sample."""
    parameters = {
        "project_id": project.id,
        "runtime_type": project.runtime_type,
    }
    service = project.runtime_config.get("systemd_unit")
    if project.runtime_type in {"systemd", "pm2"} and isinstance(service, str):
        if service.strip():
            parameters["service"] = service
    source_path = project.runtime_config.get("source_path")
    if isinstance(source_path, str) and source_path.strip():
        parameters["project_root"] = str(root)
    return parameters


def _format_env_line(key: str, value: str) -> str:
    """Format one Docker env-file line and reject unsupported multiline values."""
    if "\x00" in key or "\x00" in value or "\n" in value or "\r" in value:
        raise ValueError("Runtime environment values must be single-line strings")
    return f"{key}={value}"


async def project_environment(project_id: str) -> dict[str, str]:
    """Load and decrypt project runtime environment variables."""
    cipher = SecretCipher(settings.field_encryption_key)
    async with SessionFactory() as session:
        variables = await session.scalars(
            select(EnvironmentVariable).where(EnvironmentVariable.project_id == project_id)
        )
        return {
            variable.key: (
                cipher.decrypt(variable.encrypted_value)
                if variable.is_secret
                else variable.encrypted_value
            )
            for variable in variables
        }


async def project_git_credential(project_id: str) -> dict[str, str] | None:
    """Load and decrypt optional private Git credentials for a project."""
    cipher = SecretCipher(settings.field_encryption_key)
    async with SessionFactory() as session:
        source = await session.scalar(
            select(ProjectSource).where(ProjectSource.project_id == project_id)
        )
        if source is None or not source.encrypted_credential:
            return None
        payload = json.loads(cipher.decrypt(source.encrypted_credential))
        if not isinstance(payload, dict):
            raise ValueError("Invalid Git credential payload")
        credential_type = payload.get("type")
        value = payload.get("value")
        username = payload.get("username")
        if credential_type not in {"token", "deploy_key"} or not isinstance(value, str):
            raise ValueError("Invalid Git credential payload")
        return {
            "type": credential_type,
            "value": value,
            "username": username if isinstance(username, str) else "x-access-token",
        }


async def project_resource_policy(project_id: str) -> dict[str, Any]:
    """Load a project resource policy for deployment-time enforcement."""
    async with SessionFactory() as session:
        policy = await session.scalar(
            select(ProjectResourcePolicy).where(ProjectResourcePolicy.project_id == project_id)
        )
        return _policy_response(policy)


@contextlib.contextmanager
def _git_credential_environment(
    project_id: str,
    root: Path,
    credential: dict[str, str] | None,
) -> Iterator[dict[str, str]]:
    """Materialize temporary Git credential files and return safe env overrides."""
    del project_id
    if credential is None:
        yield {"GIT_TERMINAL_PROMPT": "0"}
        return
    shared = root / "shared"
    shared.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="git-", dir=shared) as temporary:
        directory = Path(temporary)
        if credential["type"] == "token":
            askpass = directory / "askpass.sh"
            askpass.write_text(
                "\n".join(
                    [
                        "#!/bin/sh",
                        'case "$1" in',
                        '*Username*) printf "%s\\n" "$GIT_USERNAME" ;;',
                        '*) printf "%s\\n" "$GIT_PASSWORD" ;;',
                        "esac",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            askpass.chmod(0o700)
            yield {
                "GIT_TERMINAL_PROMPT": "0",
                "GIT_ASKPASS": str(askpass),
                "GIT_USERNAME": credential.get("username", "x-access-token"),
                "GIT_PASSWORD": credential["value"],
            }
            return
        key_file = directory / "deploy_key"
        key_file.write_text(credential["value"], encoding="utf-8")
        key_file.chmod(0o600)
        yield {
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_SSH_COMMAND": (
                f"ssh -i {key_file} -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new"
            ),
        }


def _write_runtime_env_file(project_id: str, root: Path) -> str:
    """Persist project environment for runtime engines and return a relative path."""
    values = asyncio.run(project_environment(project_id))
    shared = root / "shared"
    shared.mkdir(parents=True, exist_ok=True)
    env_file = shared / "runtime.env"
    env_file.write_text(
        "\n".join(_format_env_line(key, value) for key, value in sorted(values.items()))
        + ("\n" if values else ""),
        encoding="utf-8",
    )
    env_file.chmod(0o600)
    return f"projects/{project_id}/shared/runtime.env"


def _release_environment(project_id: str) -> dict[str, str]:
    """Load project environment for build and install commands."""
    return asyncio.run(project_environment(project_id))


def _run_release_commands(
    task: Any,
    *,
    project_id: str,
    release: Path,
    runtime_config: dict[str, Any],
) -> str:
    """Execute configured install and build commands in a release directory."""
    environment = _release_environment(project_id)
    output: list[str] = []
    for stage, key in (("install", "install_command"), ("build", "build_command")):
        command = runtime_config.get(key)
        if not isinstance(command, str) or not command.strip():
            continue
        task.update_state(state="PROGRESS", meta={"progress": 35, "stage": stage})
        output.append(f"$ {command}")
        output.append(
            run_project_command(
                command,
                release,
                env_overrides=environment,
            )
        )
    return "\n".join(output)[-262144:]


def _deploy_runtime(
    task: Any,
    *,
    project_id: str,
    release_id: str,
    root: Path,
    release: Path,
    runtime_type: str,
    runtime_config: dict[str, Any],
) -> dict[str, Any]:
    """Activate a supported project runtime through the local agent."""
    env_file = _write_runtime_env_file(project_id, root)
    resource_policy = asyncio.run(project_resource_policy(project_id))
    if runtime_type == "docker":
        task.update_state(state="PROGRESS", meta={"progress": 60, "stage": "container"})
        configured_host_port = runtime_config.get(
            "upstream_port",
            runtime_config.get("host_port", 0),
        )
        start_command = runtime_config.get("start_command")
        result = agent_operation(
            "deploy_container",
            {
                "project_id": project_id,
                "release_id": release_id,
                "internal_port": int(runtime_config.get("internal_port", 8000)),
                "host_port": int(configured_host_port or 0),
                "start_command": (
                    start_command
                    if isinstance(start_command, str) and start_command.strip()
                    else None
                ),
                "env_file": env_file,
                "persistent_mounts": runtime_config.get("persistent_mounts"),
                **_policy_deploy_parameters(resource_policy, runtime_type),
            },
            timeout=1900,
        )
        assigned_host_port = result.get("host_port")
        if isinstance(assigned_host_port, int):
            asyncio.run(_persist_docker_upstream_port(project_id, assigned_host_port))
        return result
    if runtime_type == "docker_compose":
        task.update_state(state="PROGRESS", meta={"progress": 60, "stage": "compose"})
        return agent_operation(
            "deploy_compose",
            {
                "project_id": project_id,
                "release_id": release_id,
                "env_file": env_file,
            },
            timeout=1900,
        )
    if runtime_type == "static":
        output_directory = str(runtime_config.get("output_directory") or "dist")
        static_root = (release / output_directory).resolve()
        if release not in static_root.parents and static_root != release:
            raise ValueError("Static output directory escapes release")
        if not static_root.is_dir():
            raise ValueError("Static output directory does not exist")
        return {"static_root": str(static_root)}
    if runtime_type == "systemd":
        start_command = runtime_config.get("start_command")
        if not isinstance(start_command, str) or not start_command.strip():
            raise ValueError("Systemd runtime requires a start command")
        task.update_state(state="PROGRESS", meta={"progress": 60, "stage": "systemd"})
        return agent_operation(
            "deploy_systemd",
            {
                "project_id": project_id,
                "release_id": release_id,
                "start_command": start_command,
                "env_file": env_file,
                **_policy_deploy_parameters(resource_policy, runtime_type),
            },
            timeout=1900,
        )
    if runtime_type == "pm2":
        start_command = runtime_config.get("start_command")
        if not isinstance(start_command, str) or not start_command.strip():
            raise ValueError("PM2 runtime requires a start command")
        task.update_state(state="PROGRESS", meta={"progress": 60, "stage": "pm2"})
        return agent_operation(
            "deploy_pm2",
            {
                "project_id": project_id,
                "release_id": release_id,
                "start_command": start_command,
                "env_file": env_file,
                **_policy_deploy_parameters(resource_policy, runtime_type),
            },
            timeout=1900,
        )
    if runtime_type in MINECRAFT_TYPES:
        task.update_state(state="PROGRESS", meta={"progress": 60, "stage": "minecraft"})
        xmx = str(runtime_config.get("xmx", "2G"))
        memory_mb = resource_policy.get("memory_mb")
        if resource_policy.get("enabled") and memory_mb is not None:
            required_mb = _memory_limit_megabytes(xmx) + 512
            if int(memory_mb) < required_mb:
                raise ValueError("Minecraft memory policy must be at least Xmx + 512 MiB")
        return agent_operation(
            "start_minecraft",
            {
                "project_id": project_id,
                "java_version": int(runtime_config.get("java_version", 21)),
                "xms": str(runtime_config.get("xms", "1G")),
                "xmx": xmx,
                "server_jar": str(runtime_config.get("server_jar", "server.jar")),
                "launch_mode": str(runtime_config.get("launch_mode", "jar")),
                "game_port": int(runtime_config.get("game_port", 25565)),
                "eula_accepted": bool(runtime_config.get("eula_accepted", False)),
                "rcon_enabled": bool(runtime_config.get("rcon_enabled", False)),
                "rcon_port": int(runtime_config.get("rcon_port", 25575)),
                "rcon_host_port": int(runtime_config.get("rcon_host_port", 25575)),
                "rcon_password": runtime_config.get("rcon_password"),
                **_policy_deploy_parameters(resource_policy, runtime_type),
            },
            timeout=1900,
        )
    raise ValueError(f"Runtime {runtime_type!r} is not deployable yet")


async def _persist_minecraft_install(
    project_id: str,
    result: dict[str, Any],
) -> None:
    """Persist the launcher selected by a completed Forge installation."""
    async with SessionFactory() as session:
        project = await session.get(Project, project_id)
        if project is None or project.project_type not in MINECRAFT_TYPES:
            raise ValueError("Minecraft project no longer exists")
        configuration = dict(project.runtime_config)
        configuration["server_jar"] = str(result["launcher"])
        configuration["launch_mode"] = str(result["launch_mode"])
        if result.get("minecraft_version") is not None:
            configuration["minecraft_version"] = str(result["minecraft_version"])
        if result.get("forge_version") is not None:
            configuration["forge_version"] = str(result["forge_version"])
        if result.get("java_version") is not None:
            configuration["java_version"] = int(result["java_version"])
        project.runtime_config = configuration
        runtime = await session.scalar(
            select(ProjectRuntime).where(ProjectRuntime.project_id == project_id)
        )
        if runtime is not None:
            runtime.configuration = configuration
        await session.commit()


async def _persist_docker_upstream_port(project_id: str, host_port: int) -> None:
    """Persist the host port assigned to a Docker project."""
    if not 1024 <= host_port <= 65535:
        raise ValueError("Docker returned an invalid host port")
    async with SessionFactory() as session:
        project = await session.get(Project, project_id)
        if project is None or project.runtime_type != "docker":
            raise ValueError("Docker project no longer exists")
        configuration = dict(project.runtime_config)
        configuration.pop("host_port", None)
        configuration["upstream_port"] = host_port
        project.runtime_config = configuration
        runtime = await session.scalar(
            select(ProjectRuntime).where(ProjectRuntime.project_id == project_id)
        )
        if runtime is not None:
            runtime.configuration = configuration
        await session.commit()


@celery_app.task(bind=True, name="minecraft.install")
@reserved_operation
def install_minecraft_server(
    task: Any,
    *,
    project_id: str,
    java_version: int,
    installer_jar: str | None = None,
    minecraft_version: str | None = None,
    forge_version: str | None = None,
) -> dict[str, Any]:
    """Install an official or uploaded Forge server and persist its launcher."""
    task.update_state(state="PROGRESS", meta={"progress": 10, "stage": "validate"})
    task.update_state(state="PROGRESS", meta={"progress": 20, "stage": "install"})
    result = agent_operation(
        "install_minecraft",
        {
            "project_id": project_id,
            "java_version": java_version,
            "installer_jar": installer_jar,
            "minecraft_version": minecraft_version,
            "forge_version": forge_version,
            **(
                {"job_id": str(task.request.id)}
                if getattr(getattr(task, "request", None), "id", None)
                else {}
            ),
        },
        timeout=1900,
    )
    task.update_state(state="PROGRESS", meta={"progress": 90, "stage": "persist"})
    asyncio.run(_persist_minecraft_install(project_id, result))
    task.update_state(state="PROGRESS", meta={"progress": 100, "stage": "complete"})
    return result


async def _minecraft_backup_parameters(project_id: str) -> dict[str, Any] | None:
    """Load RCON parameters when the backup belongs to a Minecraft project."""
    async with SessionFactory() as session:
        project = await session.get(Project, project_id)
        if project is None or project.project_type not in MINECRAFT_TYPES:
            return None
        configuration = project.runtime_config
        password: str | None = None
        if bool(configuration.get("rcon_enabled", False)):
            variable = await session.scalar(
                select(EnvironmentVariable).where(
                    EnvironmentVariable.project_id == project_id,
                    EnvironmentVariable.key == "MINECRAFT_RCON_PASSWORD",
                )
            )
            if variable is not None:
                password = SecretCipher(settings.field_encryption_key).decrypt(
                    variable.encrypted_value
                )
        return {
            "project_id": project_id,
            "rcon_host_port": int(configuration.get("rcon_host_port", 25575)),
            "rcon_password": password,
        }


def _activate_release(root: Path, release: Path) -> None:
    """Atomically point the project current symlink at a successful release."""
    current = root / "current"
    temporary = root / ".current.tmp"
    if temporary.exists() or temporary.is_symlink():
        temporary.unlink()
    temporary.symlink_to(release, target_is_directory=True)
    temporary.replace(current)


async def _provision_postgresql(
    server: DatabaseServer,
    instance: DatabaseInstance,
    admin_password: str,
    password: str,
) -> None:
    """Provision a PostgreSQL role and database idempotently."""
    name = _database_identifier(instance.name)
    username = _database_identifier(instance.username)
    connection = await asyncpg.connect(
        host=server.host,
        port=server.port,
        user=server.admin_username,
        password=admin_password,
        database="postgres",
        ssl="require" if server.tls_enabled else False,
        timeout=15,
    )
    try:
        role_exists = await connection.fetchval(
            "SELECT 1 FROM pg_roles WHERE rolname = $1", username
        )
        password_literal = password.replace("'", "''")
        if not role_exists:
            await connection.execute(
                f"CREATE ROLE \"{username}\" LOGIN PASSWORD '{password_literal}'"
            )
        database_exists = await connection.fetchval(
            "SELECT 1 FROM pg_database WHERE datname = $1", name
        )
        if not database_exists:
            await connection.execute(f'CREATE DATABASE "{name}" OWNER "{username}"')
        await connection.execute(f'GRANT ALL PRIVILEGES ON DATABASE "{name}" TO "{username}"')
    finally:
        await connection.close()


async def _provision_mysql(
    server: DatabaseServer,
    instance: DatabaseInstance,
    admin_password: str,
    password: str,
) -> None:
    """Provision a MySQL or MariaDB user and database idempotently."""
    name = _database_identifier(instance.name)
    username = _database_identifier(instance.username)
    password_literal = password.replace("\\", "\\\\").replace("'", "''")
    connection = await asyncmy.connect(
        host=server.host,
        port=server.port,
        user=server.admin_username,
        password=admin_password,
        ssl=server.tls_enabled,
        autocommit=True,
        connect_timeout=15,
    )
    try:
        async with connection.cursor() as cursor:
            await cursor.execute(
                f"CREATE DATABASE IF NOT EXISTS `{name}` CHARACTER SET utf8mb4 "
                "COLLATE utf8mb4_unicode_ci"
            )
            await cursor.execute(
                f"CREATE USER IF NOT EXISTS '{username}'@'%' IDENTIFIED BY '{password_literal}'"
            )
            await cursor.execute(f"GRANT ALL PRIVILEGES ON `{name}`.* TO '{username}'@'%'")
            await cursor.execute("FLUSH PRIVILEGES")
    finally:
        connection.close()


async def _delete_postgresql(
    server: DatabaseServer,
    instance: DatabaseInstance,
    admin_password: str,
) -> None:
    """Drop a managed PostgreSQL database and its dedicated role."""
    name = _database_identifier(instance.name)
    username = _database_identifier(instance.username)
    connection = await asyncpg.connect(
        host=server.host,
        port=server.port,
        user=server.admin_username,
        password=admin_password,
        database="postgres",
        ssl="require" if server.tls_enabled else False,
        timeout=15,
    )
    try:
        await connection.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = $1 AND pid <> pg_backend_pid()",
            name,
        )
        await connection.execute(f'DROP DATABASE IF EXISTS "{name}"')
        await connection.execute(f'DROP ROLE IF EXISTS "{username}"')
    finally:
        await connection.close()


async def _delete_mysql(
    server: DatabaseServer,
    instance: DatabaseInstance,
    admin_password: str,
) -> None:
    """Drop a managed MySQL/MariaDB database and its dedicated user."""
    name = _database_identifier(instance.name)
    username = _database_identifier(instance.username)
    connection = await asyncmy.connect(
        host=server.host,
        port=server.port,
        user=server.admin_username,
        password=admin_password,
        ssl=server.tls_enabled,
        autocommit=True,
        connect_timeout=15,
    )
    try:
        async with connection.cursor() as cursor:
            await cursor.execute(f"DROP DATABASE IF EXISTS `{name}`")
            await cursor.execute(f"DROP USER IF EXISTS '{username}'@'%'")
    finally:
        connection.close()


async def provision_database_record(database_id: str) -> dict[str, Any]:
    """Load encrypted credentials, provision the engine, and update status."""
    cipher = SecretCipher(settings.field_encryption_key)
    async with SessionFactory() as session:
        instance = await session.get(DatabaseInstance, database_id)
        if instance is None:
            raise ValueError("Database instance not found")
        server = await session.get(DatabaseServer, instance.server_id)
        if server is None:
            raise ValueError("Database server not found")
        admin_password = cipher.decrypt(server.encrypted_admin_password)
        password = cipher.decrypt(instance.encrypted_password)
        instance.status = "provisioning"
        await session.commit()
        try:
            if server.engine == "postgresql":
                await _provision_postgresql(server, instance, admin_password, password)
            elif server.engine in {"mysql", "mariadb"}:
                await _provision_mysql(server, instance, admin_password, password)
            elif server.engine == "sqlite":
                path = sqlite_database_path(settings.storage_root, instance.id)
                path.parent.mkdir(parents=True, exist_ok=True)
                async with aiosqlite.connect(path) as connection:
                    await connection.execute("PRAGMA journal_mode=WAL")
                    await connection.execute("PRAGMA foreign_keys=ON")
                    await connection.commit()
                path.chmod(0o640)
            else:
                raise ValueError("Unsupported provisioning engine")
        except Exception:
            instance.status = "failed"
            await session.commit()
            raise
        instance.status = "ready"
        await session.commit()
        return {
            "database_id": instance.id,
            "engine": instance.engine,
            "status": instance.status,
        }


async def delete_database_record(database_id: str) -> dict[str, Any]:
    """Delete a managed database remotely, then remove matching local metadata."""
    cipher = SecretCipher(settings.field_encryption_key)
    async with SessionFactory() as session:
        instance = await session.get(DatabaseInstance, database_id)
        if instance is None:
            raise ValueError("Database instance not found")
        server = await session.get(DatabaseServer, instance.server_id)
        if server is None:
            raise ValueError("Database server not found")
        admin_password = cipher.decrypt(server.encrypted_admin_password)
        password = cipher.decrypt(instance.encrypted_password)
        dsn = (
            sqlite_connection_string(sqlite_database_path(settings.storage_root, instance.id))
            if server.engine == "sqlite"
            else connection_string(
                engine=server.engine,
                host=server.host,
                port=server.port,
                database=instance.name,
                username=instance.username,
                password=password,
            )
        )
        instance.status = "deleting"
        await session.commit()
        try:
            if server.engine == "postgresql":
                await _delete_postgresql(server, instance, admin_password)
            elif server.engine in {"mysql", "mariadb"}:
                await _delete_mysql(server, instance, admin_password)
            elif server.engine == "sqlite":
                path = sqlite_database_path(settings.storage_root, instance.id)
                path.unlink(missing_ok=True)
                path.with_name(f"{path.name}-wal").unlink(missing_ok=True)
                path.with_name(f"{path.name}-shm").unlink(missing_ok=True)
            else:
                raise ValueError("Unsupported deletion engine")
        except Exception:
            instance.status = "delete_failed"
            await session.commit()
            raise
        if instance.project_id:
            variable = await session.scalar(
                select(EnvironmentVariable).where(
                    EnvironmentVariable.project_id == instance.project_id,
                    EnvironmentVariable.key == "DATABASE_URL",
                )
            )
            if variable is not None:
                try:
                    variable_matches = cipher.decrypt(variable.encrypted_value) == dsn
                except ValueError:
                    variable_matches = False
                    logger.error("Unable to inspect attached DATABASE_URL during deletion")
                if variable_matches:
                    await session.delete(variable)
        await session.execute(
            delete(DatabaseUser).where(DatabaseUser.database_id == instance.id)
        )
        await session.delete(instance)
        await session.commit()
        return {"database_id": database_id, "deleted": True}


async def database_connection_record(database_id: str) -> dict[str, Any]:
    """Load and decrypt a managed database connection for worker use."""
    cipher = SecretCipher(settings.field_encryption_key)
    async with SessionFactory() as session:
        instance = await session.get(DatabaseInstance, database_id)
        if instance is None:
            raise ValueError("Database instance not found")
        server = await session.get(DatabaseServer, instance.server_id)
        if server is None:
            raise ValueError("Database server not found")
        if server.engine == "sqlite":
            return {
                "database_id": instance.id,
                "engine": server.engine,
                "path": str(sqlite_database_path(settings.storage_root, instance.id)),
            }
        return {
            "database_id": instance.id,
            "engine": server.engine,
            "host": server.host,
            "port": server.port,
            "database": instance.name,
            "username": instance.username,
            "password": cipher.decrypt(instance.encrypted_password),
        }


def _backup_sqlite_database(source: Path, target: Path) -> None:
    """Create and verify a consistent SQLite backup using the native backup API."""
    if not source.is_file() or source.is_symlink():
        raise ValueError("SQLite database file is unavailable")
    source_connection = sqlite3.connect(f"file:{source}?mode=ro", uri=True, timeout=15)
    try:
        target_connection = sqlite3.connect(target, timeout=15)
        try:
            source_connection.backup(target_connection)
            result = target_connection.execute("PRAGMA integrity_check").fetchone()
            if result is None or result[0] != "ok":
                raise ValueError("SQLite backup integrity check failed")
        finally:
            target_connection.close()
    finally:
        source_connection.close()
    target.chmod(0o640)


def _restore_sqlite_database(source: Path, target: Path) -> None:
    """Validate and atomically replace a stopped SQLite database."""
    if not source.is_file() or source.is_symlink():
        raise ValueError("SQLite restore file is unavailable")
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        prefix=f".{target.name}.",
        suffix=".restore",
        dir=target.parent,
        delete=False,
    ) as handle:
        temporary = Path(handle.name)
    try:
        _backup_sqlite_database(source, temporary)
        target.with_name(f"{target.name}-wal").unlink(missing_ok=True)
        target.with_name(f"{target.name}-shm").unlink(missing_ok=True)
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)


@celery_app.task(bind=True, name="projects.git_deploy")
def git_deploy(
    task: Any,
    *,
    project_id: str,
    repository_url: str,
    branch: str,
    project_root: str,
    release_id: str,
    runtime_type: str,
    runtime_config: dict[str, Any],
) -> dict[str, Any]:
    """Clone a Git revision into an immutable project release."""
    root = Path(project_root).resolve()
    release = (root / "releases" / release_id).resolve()
    if root not in release.parents:
        raise ValueError("Release path escapes project root")
    task.update_state(state="PROGRESS", meta={"progress": 10, "stage": "prepare"})
    release.mkdir(parents=True, exist_ok=False)
    task.update_state(state="PROGRESS", meta={"progress": 25, "stage": "clone"})
    credential = asyncio.run(project_git_credential(project_id))
    with _git_credential_environment(project_id, root, credential) as git_environment:
        log = run(
            [
                "git",
                "clone",
                "--depth",
                "1",
                "--branch",
                branch,
                "--",
                repository_url,
                str(release),
            ],
            root,
            env_overrides=git_environment,
        )
    command_log = _run_release_commands(
        task,
        project_id=project_id,
        release=release,
        runtime_config=runtime_config,
    )
    runtime_result = _deploy_runtime(
        task,
        project_id=project_id,
        release_id=release_id,
        root=root,
        release=release,
        runtime_type=runtime_type,
        runtime_config=runtime_config,
    )
    _activate_release(root, release)
    task.update_state(state="PROGRESS", meta={"progress": 100, "stage": "complete"})
    return {
        "project_id": project_id,
        "release_id": release_id,
        "release_path": str(release),
        "log": "\n".join(part for part in (log, command_log) if part),
        "runtime": runtime_result,
    }


@celery_app.task(bind=True, name="projects.archive_deploy")
def archive_deploy(
    task: Any,
    *,
    project_id: str,
    archive_path: str,
    project_root: str,
    release_id: str,
    runtime_type: str,
    runtime_config: dict[str, Any],
) -> dict[str, Any]:
    """Extract a validated ZIP upload and deploy it as an immutable release."""
    root = Path(project_root).resolve()
    release = (root / "releases" / release_id).resolve()
    incoming = Path(archive_path).resolve()
    if root not in release.parents or root not in incoming.parents:
        raise ValueError("Deployment path escapes project root")
    release.mkdir(parents=True, exist_ok=False)
    task.update_state(state="PROGRESS", meta={"progress": 20, "stage": "extract"})
    try:
        extract_zip_safely(incoming, release)
        entries = list(release.iterdir())
        if len(entries) == 1 and entries[0].is_dir() and not entries[0].is_symlink():
            nested = entries[0]
            for item in nested.iterdir():
                item.replace(release / item.name)
            nested.rmdir()
        command_log = _run_release_commands(
            task,
            project_id=project_id,
            release=release,
            runtime_config=runtime_config,
        )
        runtime_result = _deploy_runtime(
            task,
            project_id=project_id,
            release_id=release_id,
            root=root,
            release=release,
            runtime_type=runtime_type,
            runtime_config=runtime_config,
        )
        _activate_release(root, release)
    except Exception:
        shutil.rmtree(release, ignore_errors=True)
        raise
    finally:
        incoming.unlink(missing_ok=True)
    task.update_state(state="PROGRESS", meta={"progress": 100, "stage": "complete"})
    return {
        "project_id": project_id,
        "release_id": release_id,
        "release_path": str(release),
        "log": "\n".join(
            part for part in ("Archive deployment completed", command_log) if part
        ),
        "runtime": runtime_result,
    }


@celery_app.task(bind=True, name="projects.rollback")
def rollback_release(
    task: Any,
    *,
    project_id: str,
    project_root: str,
    release_id: str,
    runtime_type: str,
    runtime_config: dict[str, Any],
) -> dict[str, Any]:
    """Reactivate a previously successful immutable release."""
    root = Path(project_root).resolve()
    release = (root / "releases" / release_id).resolve()
    if root not in release.parents or not release.is_dir():
        raise ValueError("Rollback release is unavailable")
    runtime_result = _deploy_runtime(
        task,
        project_id=project_id,
        release_id=release_id,
        root=root,
        release=release,
        runtime_type=runtime_type,
        runtime_config=runtime_config,
    )
    _activate_release(root, release)
    task.update_state(state="PROGRESS", meta={"progress": 100, "stage": "complete"})
    return {
        "project_id": project_id,
        "release_id": release_id,
        "release_path": str(release),
        "log": "Rollback completed",
        "runtime": runtime_result,
    }


async def minecraft_backup_configuration(project_id: str) -> dict[str, Any]:
    """Capture non-secret runtime metadata for the same backup point."""
    async with SessionFactory() as session:
        project = await session.get(Project, project_id)
        return dict(project.runtime_config) if project else {}


@celery_app.task(bind=True, name="backups.create_project")
@reserved_operation
def backup_project(
    task: Any,
    *,
    backup_id: str,
    source: str,
    backup_root: str,
    project_id: str,
    include_items: list[str] | None = None,
    encryption_enabled: bool = False,
    retention_count: int | None = None,
) -> dict[str, Any]:
    """Create a timestamped project backup manifest and archive."""
    task.update_state(state="PROGRESS", meta={"progress": 5, "stage": "prepare"})
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    target = Path(backup_root) / "projects" / project_id / f"{timestamp}-{backup_id}.tar.gz"
    prepared_minecraft = False
    minecraft_parameters: dict[str, Any] | None = None
    try:
        try:
            minecraft_parameters = asyncio.run(_minecraft_backup_parameters(project_id))
            if minecraft_parameters is not None:
                task_id = getattr(getattr(task, "request", None), "id", None)
                if task_id:
                    asyncio.run(persist_phase(str(task_id), "world_backup", 10))
                preparation = agent_operation(
                    "minecraft_backup",
                    {**minecraft_parameters, "action": "prepare"},
                    timeout=150,
                )
                prepared_minecraft = bool(preparation.get("prepared", False))
            task.update_state(state="PROGRESS", meta={"progress": 20, "stage": "archive"})
            manifest = create_backup(
                Path(source),
                target,
                backup_type="project",
                project_id=project_id,
                include_items=include_items,
                encryption_key=settings.field_encryption_key if encryption_enabled else None,
            )
        finally:
            if prepared_minecraft and minecraft_parameters is not None:
                agent_operation(
                    "minecraft_backup",
                    {**minecraft_parameters, "action": "resume"},
                )
    except Exception as exc:
        asyncio.run(mark_backup_failed(backup_id, str(exc)))
        raise
    artifact = Path(str(manifest["artifact_path"]))
    if minecraft_parameters is not None:
        manifest["minecraft_configuration"] = asyncio.run(
            minecraft_backup_configuration(project_id)
        )
        artifact.with_suffix(artifact.suffix + ".manifest.json").write_text(
            json.dumps(manifest), encoding="utf-8"
        )
    asyncio.run(mark_backup_ready(backup_id, manifest, artifact))
    if retention_count is not None:
        asyncio.run(prune_project_backups(project_id, retention_count))
    task.update_state(state="PROGRESS", meta={"progress": 100, "stage": "complete"})
    return {**manifest, "backup_id": backup_id, "path": str(artifact)}


async def mark_backup_ready(
    backup_id: str,
    manifest: dict[str, Any],
    artifact: Path,
) -> None:
    """Persist completed backup metadata without requiring job polling."""
    async with SessionFactory() as session:
        backup = await session.get(Backup, backup_id)
        if backup is None:
            return
        backup.status = "ready"
        backup.path = str(artifact)
        backup.size_bytes = int(manifest["size_bytes"])
        backup.checksum = str(manifest["checksum"])
        backup.manifest = manifest
        await session.commit()


async def mark_backup_failed(backup_id: str, error: str) -> None:
    """Persist a failed backup state without leaking task details."""
    subject = "неизвестного ресурса"
    async with SessionFactory() as session:
        backup = await session.get(Backup, backup_id)
        if backup is None:
            return
        backup.status = "failed"
        backup.manifest = {"error": error[:1000]}
        if backup.project_id:
            project = await session.get(Project, backup.project_id)
            if project is not None:
                subject = f"проекта {project.name}"
        elif backup.database_id:
            database = await session.get(DatabaseInstance, backup.database_id)
            if database is not None:
                subject = f"базы данных {database.name}"
        await session.commit()
    await create_owner_alert(
        notification_type="backup.failed",
        severity="error",
        title="Ошибка резервного копирования",
        message=f"Не удалось создать резервную копию {subject}.",
        resource_type="backup",
        resource_id=backup_id,
    )


async def prune_project_backups(project_id: str, keep: int) -> None:
    """Delete old project backup rows and artifacts beyond retention."""
    if not 1 <= keep <= 100:
        raise ValueError("Invalid backup retention")
    async with SessionFactory() as session:
        backups = list(
            await session.scalars(
                select(Backup)
                .where(
                    Backup.project_id == project_id,
                    Backup.status == "ready",
                )
                .order_by(Backup.created_at.desc())
            )
        )
        root = (settings.storage_root / "backups").resolve()
        for backup in backups[keep:]:
            if backup.path:
                path = Path(backup.path).resolve()
                if root in path.parents:
                    path.unlink(missing_ok=True)
                    path.with_suffix(path.suffix + ".manifest.json").unlink(missing_ok=True)
            await session.delete(backup)
        await session.commit()


async def prune_database_backups(database_id: str, keep: int) -> None:
    """Delete old database backup rows and artifacts beyond retention."""
    if not 1 <= keep <= 100:
        raise ValueError("Invalid backup retention")
    async with SessionFactory() as session:
        backups = list(
            await session.scalars(
                select(Backup)
                .where(
                    Backup.database_id == database_id,
                    Backup.status == "ready",
                )
                .order_by(Backup.created_at.desc())
            )
        )
        root = (settings.storage_root / "backups").resolve()
        for backup in backups[keep:]:
            if backup.path:
                path = Path(backup.path).resolve()
                if root in path.parents:
                    path.unlink(missing_ok=True)
                    path.with_suffix(path.suffix + ".manifest.json").unlink(missing_ok=True)
            await session.delete(backup)
        await session.commit()


def _copy_backup_tree(source: Path, destination: Path) -> int:
    """Copy a directory tree into staging without following symlinks."""
    if not source.exists():
        return 0
    if not source.is_dir() or source.is_symlink():
        raise ValueError("Full backup source must be a regular directory")
    copied = 0
    destination.mkdir(parents=True, exist_ok=True)
    for path in source.rglob("*"):
        relative = path.relative_to(source)
        target = destination / relative
        if path.is_symlink():
            continue
        if path.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        if not path.is_file():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target, follow_symlinks=False)
        copied += 1
    return copied


def _export_panel_database(target_root: Path) -> tuple[str, Path]:
    """Export the panel database into full backup staging."""
    url = make_url(settings.database_url)
    driver = url.get_backend_name()
    target_root.mkdir(parents=True, exist_ok=True)
    if driver == "sqlite":
        database = url.database
        if not database:
            raise ValueError("SQLite database path is not configured")
        source = Path(database).resolve()
        if not source.is_file():
            raise ValueError("SQLite panel database does not exist")
        target = target_root / "panel.sqlite3"
        shutil.copy2(source, target)
        return "sqlite", target
    if driver == "postgresql":
        target = target_root / "panel-postgresql.dump"
        run(
            [
                "pg_dump",
                "--host",
                url.host or "localhost",
                "--port",
                str(url.port or 5432),
                "--username",
                url.username or "",
                "--format",
                "custom",
                "--file",
                str(target),
                url.database or "",
            ],
            target_root,
            env_overrides={"PGPASSWORD": url.password or ""},
        )
        return "postgresql", target
    if driver in {"mysql", "mariadb"}:
        target = target_root / "panel-mysql.sql"
        run(
            [
                "mysqldump",
                "--host",
                url.host or "localhost",
                "--port",
                str(url.port or 3306),
                "--user",
                url.username or "",
                "--single-transaction",
                "--result-file",
                str(target),
                url.database or "",
            ],
            target_root,
            env_overrides={"MYSQL_PWD": url.password or ""},
        )
        return driver, target
    raise ValueError("Unsupported panel database engine")


def _write_full_backup_manifest(
    artifact: Path,
    *,
    included_items: list[str],
    item_counts: dict[str, int],
    panel_database_engine: str | None,
) -> dict[str, Any]:
    """Build and persist a full-panel backup manifest."""
    manifest = {
        "format_version": BACKUP_FORMAT_VERSION,
        "type": "full",
        "created_at": datetime.now(UTC).isoformat(),
        "project_id": None,
        "database_id": None,
        "included_items": included_items,
        "item_counts": item_counts,
        "panel_database_engine": panel_database_engine,
        "size_bytes": artifact.stat().st_size,
        "checksum": sha256_file(artifact),
        "encrypted": False,
        "encryption": None,
        "app_version": APP_VERSION,
        "restore_compatibility": RESTORE_COMPATIBILITY,
        "artifact_path": str(artifact),
    }
    artifact.with_suffix(artifact.suffix + ".manifest.json").write_text(
        json.dumps(manifest, indent=2),
        encoding="utf-8",
    )
    return manifest


@celery_app.task(bind=True, name="backups.create_full")
def backup_full_panel(
    task: Any,
    *,
    backup_id: str,
    backup_root: str,
    include_items: list[str] | None = None,
    encryption_enabled: bool = False,
) -> dict[str, Any]:
    """Create a full-panel backup for configs, storage state and panel database."""
    requested = include_items or ["all"]
    if not requested or ("all" in requested and len(requested) != 1):
        raise ValueError("Full backup items must be either all or an explicit list")
    selected = sorted(FULL_BACKUP_ITEMS) if requested == ["all"] else requested
    if any(item not in FULL_BACKUP_ITEMS for item in selected):
        raise ValueError("Unsupported full backup item")
    task.update_state(state="PROGRESS", meta={"progress": 5, "stage": "prepare"})
    root = Path(backup_root).resolve()
    target_root = root / "full"
    target_root.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    target = target_root / f"{timestamp}-{backup_id}.tar.gz"
    panel_database_engine: str | None = None
    try:
        with tempfile.TemporaryDirectory(prefix="full-backup-", dir=target_root) as temporary:
            staging = Path(temporary) / "nectarine-full"
            staging.mkdir()
            item_counts: dict[str, int] = {}
            if "panel_db" in selected:
                task.update_state(state="PROGRESS", meta={"progress": 15, "stage": "panel_db"})
                panel_database_engine, panel_dump = _export_panel_database(staging / "panel_db")
                item_counts["panel_db"] = int(panel_dump.is_file())
            if "panel_config" in selected:
                item_counts["panel_config"] = _copy_backup_tree(
                    settings.config_root,
                    staging / "panel_config",
                )
            if "nginx_configs" in selected:
                item_counts["nginx_configs"] = _copy_backup_tree(
                    settings.nginx_config_root,
                    staging / "nginx_configs",
                )
            storage_items = {
                "project_files": ("projects", settings.storage_root / "projects"),
                "minecraft_files": ("minecraft", settings.storage_root / "minecraft"),
                "sqlite_databases": (
                    "databases/sqlite",
                    settings.storage_root / "databases" / "sqlite",
                ),
                "state": ("state", settings.storage_root / "state"),
            }
            for item, (relative, source) in storage_items.items():
                if item in selected:
                    item_counts[item] = _copy_backup_tree(
                        source,
                        staging / "storage" / relative,
                    )
            task.update_state(state="PROGRESS", meta={"progress": 70, "stage": "archive"})
            create_tar_gz_safely(staging, target, staging.parent)
        artifact = target
        encrypted = encryption_enabled
        if encryption_enabled:
            if not settings.field_encryption_key:
                raise ValueError("Backup encryption key is not configured")
            artifact = target.with_suffix(target.suffix + ".enc")

            encrypt_backup(target, artifact, settings.field_encryption_key)
            target.unlink()
        manifest = _write_full_backup_manifest(
            artifact,
            included_items=requested,
            item_counts=item_counts,
            panel_database_engine=panel_database_engine,
        )
        if encrypted:
            manifest["encrypted"] = True
            manifest["encryption"] = "aes-256-gcm"
            artifact.with_suffix(artifact.suffix + ".manifest.json").write_text(
                json.dumps(manifest, indent=2),
                encoding="utf-8",
            )
        asyncio.run(mark_backup_ready(backup_id, manifest, artifact))
    except Exception as exc:
        target.unlink(missing_ok=True)
        target.with_suffix(target.suffix + ".enc").unlink(missing_ok=True)
        asyncio.run(mark_backup_failed(backup_id, str(exc)))
        raise
    task.update_state(state="PROGRESS", meta={"progress": 100, "stage": "complete"})
    return {**manifest, "backup_id": backup_id, "path": str(artifact)}


@celery_app.task(name="backups.cleanup")
def cleanup_backups(*, backup_directory: str, keep: int) -> dict[str, int]:
    """Remove oldest backup archives beyond the retention count."""
    if keep < 1:
        raise ValueError("Retention must keep at least one backup")
    directory = Path(backup_directory).resolve()
    archives = sorted(directory.glob("*.tar.gz"), key=lambda path: path.stat().st_mtime)
    removed = 0
    for archive in archives[:-keep]:
        archive.unlink()
        manifest = archive.with_suffix(archive.suffix + ".manifest.json")
        if manifest.exists():
            manifest.unlink()
        removed += 1
    return {"removed": removed}


@celery_app.task(bind=True, name="backups.restore_project")
@reserved_operation
def restore_project_backup(
    task: Any,
    *,
    backup_id: str,
    project_id: str,
    archive_path: str,
    destination: str,
) -> dict[str, Any]:
    """Verify and atomically restore a project backup."""
    task.update_state(state="PROGRESS", meta={"progress": 10, "stage": "verify"})
    archive = Path(archive_path)
    manifest = archive.with_suffix(archive.suffix + ".manifest.json")

    async def restore_context() -> dict[str, Any] | None:
        """Resolve local-only lifecycle parameters for a Minecraft restore."""
        async with SessionFactory() as session:
            project = await session.get(Project, project_id)
            if project is None or project.runtime_type not in MINECRAFT_TYPES:
                return None
            return await minecraft_parameters(project, session, settings)

    minecraft_launch = asyncio.run(restore_context())
    if minecraft_launch:
        agent_operation("minecraft_stop", stop_parameters(minecraft_launch), timeout=150)
    task.update_state(state="PROGRESS", meta={"progress": 35, "stage": "extract"})
    restore_backup(
        archive,
        Path(destination),
        manifest,
        encryption_key=settings.field_encryption_key,
        expected_project_id=project_id,
    )
    task.update_state(state="PROGRESS", meta={"progress": 100, "stage": "complete"})
    if minecraft_launch:
        restored_configuration = json.loads(manifest.read_text(encoding="utf-8")).get(
            "minecraft_configuration"
        )

        async def persist_restored_configuration() -> None:
            """Keep launcher metadata consistent with the restored server tree."""
            async with SessionFactory() as session:
                project = await session.get(Project, project_id)
                runtime = await session.scalar(
                    select(ProjectRuntime).where(ProjectRuntime.project_id == project_id)
                )
                if project is not None:
                    project.status = "stopped"
                    if isinstance(restored_configuration, dict):
                        project.runtime_config = restored_configuration
                        if runtime:
                            runtime.configuration = restored_configuration
                    await session.commit()

        asyncio.run(persist_restored_configuration())
    return {"backup_id": backup_id, "restored": True}


@celery_app.task(bind=True, name="backups.import")
def import_project_backup(
    task: Any,
    *,
    backup_id: str,
    project_id: str,
    incoming_path: str,
    backup_root: str,
) -> dict[str, Any]:
    """Validate and register an uploaded project backup artifact."""
    incoming = Path(incoming_path).resolve()
    root = Path(backup_root).resolve()
    if root not in incoming.parents or not incoming.is_file():
        raise ValueError("Imported backup escapes backup storage")
    task.update_state(state="PROGRESS", meta={"progress": 15, "stage": "validate"})
    try:
        encrypted = validate_backup_archive(
            incoming,
            encryption_key=settings.field_encryption_key,
        )
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        suffix = ".tar.gz.enc" if encrypted else ".tar.gz"
        artifact = root / "projects" / project_id / f"{timestamp}-{backup_id}{suffix}"
        artifact.parent.mkdir(parents=True, exist_ok=True)
        incoming.replace(artifact)
        manifest = {
            "format_version": BACKUP_FORMAT_VERSION,
            "type": "project",
            "created_at": datetime.now(UTC).isoformat(),
            "project_id": project_id,
            "database_id": None,
            "included_items": ["imported"],
            "size_bytes": artifact.stat().st_size,
            "checksum": sha256_file(artifact),
            "encrypted": encrypted,
            "encryption": "aes-256-gcm" if encrypted else None,
            "app_version": APP_VERSION,
            "restore_compatibility": RESTORE_COMPATIBILITY,
            "artifact_path": str(artifact),
        }
        artifact.with_suffix(artifact.suffix + ".manifest.json").write_text(
            json.dumps(manifest, indent=2),
            encoding="utf-8",
        )
        asyncio.run(mark_backup_ready(backup_id, manifest, artifact))
    except Exception as exc:
        incoming.unlink(missing_ok=True)
        asyncio.run(mark_backup_failed(backup_id, str(exc)))
        raise
    task.update_state(state="PROGRESS", meta={"progress": 100, "stage": "complete"})
    return {**manifest, "backup_id": backup_id, "path": str(artifact)}


@celery_app.task(bind=True, name="files.archive")
@reserved_operation
def archive_files(
    task: Any,
    *,
    project_root: str,
    source: str,
    destination: str,
) -> dict[str, Any]:
    """Create a ZIP or TAR.GZ archive without following symbolic links."""
    task.update_state(state="PROGRESS", meta={"progress": 10, "stage": "scan"})
    result = agent_operation(
        "archive_path",
        {"root": project_root, "source": source, "destination": destination},
        timeout=3600,
    )
    task.update_state(state="PROGRESS", meta={"progress": 100, "stage": "complete"})
    return result


@celery_app.task(bind=True, name="files.extract")
@reserved_operation
def extract_files(
    task: Any,
    *,
    project_root: str,
    source: str,
    destination: str,
) -> dict[str, Any]:
    """Extract a validated ZIP or TAR.GZ archive into an empty directory."""
    managed_root = (settings.storage_root / "projects").resolve()
    resolved_root = Path(project_root).resolve()
    if resolved_root == managed_root or managed_root not in resolved_root.parents:
        task.update_state(state="PROGRESS", meta={"progress": 15, "stage": "validate"})
        result = agent_operation(
            "extract_archive",
            {"root": project_root, "source": source, "destination": destination},
            timeout=3600,
        )
        task.update_state(state="PROGRESS", meta={"progress": 100, "stage": "complete"})
        return result
    _, source_path = _worker_project_path(project_root, source)
    _, destination_path = _worker_project_path(project_root, destination)
    source_name = source_path.name.lower()
    if not source_path.is_file() or not (
        source_name.endswith(".zip") or source_name.endswith(".tar.gz")
    ):
        raise ValueError("ZIP or TAR.GZ archive is unavailable")
    destination_existed = destination_path.exists()
    if destination_path.exists():
        if not destination_path.is_dir() or any(destination_path.iterdir()):
            raise ValueError("Extraction destination must be an empty directory")
    destination_path.mkdir(parents=True, exist_ok=True)
    task.update_state(state="PROGRESS", meta={"progress": 15, "stage": "validate"})
    try:
        if source_name.endswith(".tar.gz"):
            extract_tar_safely(source_path, destination_path)
        else:
            extract_zip_safely(source_path, destination_path)
    except Exception:
        shutil.rmtree(destination_path, ignore_errors=True)
        if destination_existed:
            destination_path.mkdir(parents=True, exist_ok=True)
        raise
    task.update_state(state="PROGRESS", meta={"progress": 100, "stage": "complete"})
    return {"path": str(destination_path)}


async def _store_domain_certificate(
    domain_id: str,
    status: str,
    metadata: dict[str, Any] | None,
) -> None:
    """Persist domain and certificate state independently of browser polling."""
    now = datetime.now(UTC)
    async with SessionFactory() as session:
        domain = await session.get(Domain, domain_id)
        if domain is None:
            return
        domain.ssl_status = status
        certificate = await session.scalar(
            select(SslCertificate).where(SslCertificate.domain_id == domain.id)
        )
        if metadata is not None:
            expires_value = metadata.get("expires_at")
            fingerprint = metadata.get("fingerprint_sha256")
            if not isinstance(expires_value, str) or not isinstance(fingerprint, str):
                raise ValueError("Certificate metadata is incomplete")
            expires_at = datetime.fromisoformat(expires_value)
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=UTC)
            domain.certificate_expires_at = expires_at
            if certificate is None:
                certificate = SslCertificate(domain_id=domain.id)
                session.add(certificate)
            certificate.status = status
            certificate.issued_at = certificate.issued_at or now
            certificate.expires_at = expires_at
            certificate.fingerprint_sha256 = fingerprint
        elif certificate is not None:
            certificate.status = status
        await session.commit()


@celery_app.task(bind=True, name="domains.configure")
def configure_domain(
    task: Any,
    *,
    domain_id: str,
    hostname: str,
    upstream_port: int | None,
    issue_ssl: bool,
    email: str | None,
    static_root: str | None = None,
) -> dict[str, Any]:
    """Configure Nginx and optionally issue a Let's Encrypt certificate."""
    operation = "configure_static_site" if static_root else "configure_proxy"
    base_parameters: dict[str, Any] = {"hostname": hostname}
    if static_root:
        base_parameters["static_root"] = static_root
    else:
        if upstream_port is None:
            raise ValueError("Upstream port is required for proxy domains")
        base_parameters["upstream_port"] = upstream_port
    task.update_state(state="PROGRESS", meta={"progress": 10, "stage": "nginx"})
    certificate: dict[str, Any] | None = None
    try:
        agent_operation(operation, {**base_parameters, "ssl_enabled": False})
        agent_operation("validate_nginx")
        agent_operation("reload_nginx")
        if issue_ssl:
            if not email:
                raise ValueError("Certificate email is required")
            task.update_state(state="PROGRESS", meta={"progress": 45, "stage": "certificate"})
            agent_operation(
                "issue_certificate",
                {"hostname": hostname, "email": email},
                timeout=330,
            )
            task.update_state(state="PROGRESS", meta={"progress": 80, "stage": "https"})
            agent_operation(operation, {**base_parameters, "ssl_enabled": True})
            agent_operation("validate_nginx")
            agent_operation("reload_nginx")
            certificate = agent_operation(
                "certificate_info",
                {"hostname": hostname},
            )
    except Exception:
        agent_operation("remove_nginx_config", {"hostname": hostname})
        if issue_ssl:
            with contextlib.suppress(Exception):
                agent_operation("remove_certificate", {"hostname": hostname}, timeout=330)
        try:
            agent_operation("reload_nginx")
        except Exception:
            logger.exception("Failed to reload Nginx during domain rollback")
        asyncio.run(_store_domain_certificate(domain_id, "failed", None))
        raise
    status = "active" if issue_ssl else "disabled"
    asyncio.run(_store_domain_certificate(domain_id, status, certificate))
    task.update_state(state="PROGRESS", meta={"progress": 100, "stage": "complete"})
    return {
        "domain_id": domain_id,
        "hostname": hostname,
        "ssl_status": status,
        **(certificate or {}),
    }


@celery_app.task(bind=True, name="databases.provision")
def provision_database(task: Any, *, database_id: str) -> dict[str, Any]:
    """Provision a managed PostgreSQL or MySQL database."""
    task.update_state(state="PROGRESS", meta={"progress": 10, "stage": "connect"})
    result = asyncio.run(provision_database_record(database_id))
    task.update_state(state="PROGRESS", meta={"progress": 100, "stage": "complete"})
    return result


@celery_app.task(bind=True, name="databases.delete")
def delete_database(task: Any, *, database_id: str) -> dict[str, Any]:
    """Delete a managed database and its dedicated credentials."""
    task.update_state(state="PROGRESS", meta={"progress": 10, "stage": "connect"})
    result = asyncio.run(delete_database_record(database_id))
    task.update_state(state="PROGRESS", meta={"progress": 100, "stage": "complete"})
    return result


def _export_database_artifact(
    task: Any,
    *,
    database_id: str,
    backup_id: str,
) -> dict[str, Any]:
    """Export a managed database artifact and return its manifest."""
    connection = asyncio.run(database_connection_record(database_id))
    target_root = (settings.storage_root / "backups" / "databases" / database_id).resolve()
    target_root.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    task.update_state(state="PROGRESS", meta={"progress": 15, "stage": "dump"})
    if connection["engine"] == "postgresql":
        target = target_root / f"{timestamp}.dump"
        run(
            [
                "pg_dump",
                "--host",
                str(connection["host"]),
                "--port",
                str(connection["port"]),
                "--username",
                str(connection["username"]),
                "--format",
                "custom",
                "--file",
                str(target),
                str(connection["database"]),
            ],
            target_root,
            env_overrides={"PGPASSWORD": str(connection["password"])},
        )
    elif connection["engine"] in {"mysql", "mariadb"}:
        target = target_root / f"{timestamp}.sql"
        run(
            [
                "mysqldump",
                "--host",
                str(connection["host"]),
                "--port",
                str(connection["port"]),
                "--user",
                str(connection["username"]),
                "--single-transaction",
                "--result-file",
                str(target),
                str(connection["database"]),
            ],
            target_root,
            env_overrides={"MYSQL_PWD": str(connection["password"])},
        )
    elif connection["engine"] == "sqlite":
        target = target_root / f"{timestamp}.sqlite3"
        _backup_sqlite_database(Path(str(connection["path"])), target)
    else:
        raise ValueError("Unsupported export engine")
    checksum = sha256_file(target)
    manifest = {
        "format_version": BACKUP_FORMAT_VERSION,
        "type": "database",
        "created_at": datetime.now(UTC).isoformat(),
        "project_id": None,
        "database_id": database_id,
        "included_items": [target.name],
        "size_bytes": target.stat().st_size,
        "checksum": checksum,
        "app_version": APP_VERSION,
        "restore_compatibility": RESTORE_COMPATIBILITY,
        "engine": connection["engine"],
    }
    target.with_suffix(target.suffix + ".manifest.json").write_text(
        json.dumps(manifest, indent=2),
        encoding="utf-8",
    )
    task.update_state(state="PROGRESS", meta={"progress": 100, "stage": "complete"})
    return {**manifest, "backup_id": backup_id, "path": str(target)}


@celery_app.task(bind=True, name="databases.export")
def export_database(
    task: Any,
    *,
    database_id: str,
    backup_id: str,
    retention_count: int | None = None,
) -> dict[str, Any]:
    """Export a managed database and persist backup readiness independently."""
    try:
        result = _export_database_artifact(
            task,
            database_id=database_id,
            backup_id=backup_id,
        )
    except Exception as exc:
        asyncio.run(mark_backup_failed(backup_id, str(exc)))
        raise
    asyncio.run(mark_backup_ready(backup_id, result, Path(str(result["path"]))))
    if retention_count is not None:
        asyncio.run(prune_database_backups(database_id, retention_count))
    return result


@celery_app.task(bind=True, name="databases.import")
def import_database(
    task: Any,
    *,
    database_id: str,
    dump_path: str,
) -> dict[str, Any]:
    """Restore a validated dump into a managed database."""
    connection = asyncio.run(database_connection_record(database_id))
    root = settings.storage_root.resolve()
    source = Path(dump_path).resolve()
    if root not in source.parents or not source.is_file():
        raise ValueError("Import file escapes panel storage")
    task.update_state(state="PROGRESS", meta={"progress": 15, "stage": "restore"})
    try:
        if connection["engine"] == "postgresql":
            run(
                [
                    "pg_restore",
                    "--host",
                    str(connection["host"]),
                    "--port",
                    str(connection["port"]),
                    "--username",
                    str(connection["username"]),
                    "--dbname",
                    str(connection["database"]),
                    "--clean",
                    "--if-exists",
                    "--no-owner",
                    str(source),
                ],
                source.parent,
                env_overrides={"PGPASSWORD": str(connection["password"])},
            )
        elif connection["engine"] in {"mysql", "mariadb"}:
            run(
                [
                    "mysql",
                    "--host",
                    str(connection["host"]),
                    "--port",
                    str(connection["port"]),
                    "--user",
                    str(connection["username"]),
                    str(connection["database"]),
                ],
                source.parent,
                env_overrides={"MYSQL_PWD": str(connection["password"])},
                stdin_path=source,
            )
        elif connection["engine"] == "sqlite":
            _restore_sqlite_database(
                source,
                Path(str(connection["path"])),
            )
        else:
            raise ValueError("Unsupported import engine")
    finally:
        source.unlink(missing_ok=True)
    task.update_state(state="PROGRESS", meta={"progress": 100, "stage": "complete"})
    return {"database_id": database_id, "restored": True}


async def store_host_metrics(values: dict[str, Any]) -> dict[str, Any]:
    """Persist host metrics and deduplicated resource alerts."""
    now = datetime.now(UTC)
    deliveries: list[tuple[str, str]] = []
    async with SessionFactory() as session:
        disk_threshold, memory_threshold = await alert_thresholds(session, settings)
        session.add(MetricSample(project_id=None, captured_at=now, values=values))
        alerts = [
            (
                "host.high_disk",
                float(values.get("disk_percent", 0)),
                disk_threshold,
                "Высокая загрузка диска",
            ),
            (
                "host.high_memory",
                float(values.get("memory_percent", 0)),
                memory_threshold,
                "Высокая загрузка памяти",
            ),
        ]
        for alert_type, current, threshold, title in alerts:
            if current < threshold:
                continue
            existing = await session.scalar(
                select(Notification)
                .where(
                    Notification.notification_type == alert_type,
                    Notification.read_at.is_(None),
                )
                .order_by(Notification.created_at.desc())
            )
            if existing is None:
                message = f"Текущее значение: {current:.1f}%. Порог: {threshold:.1f}%."
                session.add(
                    Notification(
                        notification_type=alert_type,
                        severity="warning",
                        title=title,
                        message=message,
                        resource_type="host",
                    )
                )
                deliveries.append((title, message))
        await session.commit()
    for title, message in deliveries:
        enqueue_telegram_alert(title, message)
    return values


async def store_project_metrics() -> dict[str, int]:
    """Persist project disk and healthcheck metrics with deduplicated alerts."""
    now = datetime.now(UTC)
    checked = 0
    failed = 0
    deliveries: list[tuple[str, str]] = []
    async with SessionFactory() as session:
        projects = list(await session.scalars(select(Project)))
        policies = {
            policy.project_id: policy
            for policy in await session.scalars(select(ProjectResourcePolicy))
        }
        async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
            for project in projects:
                root = runtime_root(settings.storage_root, project)
                policy = policies.get(project.id)
                values: dict[str, Any] = {
                    "runtime_type": project.runtime_type,
                    "status": project.status,
                    "disk_bytes": _directory_size(root),
                    "health_status": "not_configured",
                    "health_status_code": None,
                    "resource_policy": {
                        **_policy_response(policy),
                        "enforcement": (
                            "monitor_only"
                            if project.runtime_type == "docker_compose"
                            else "enforced"
                        )
                        if policy is not None and policy.enabled
                        else "disabled",
                    },
                    "resource_violations": [],
                }
                try:
                    values.update(
                        await asyncio.to_thread(
                            agent_operation,
                            "project_metrics",
                            _project_metric_parameters(project, root),
                        )
                    )
                except Exception as exc:
                    logger.info(
                        "Project resource metrics unavailable",
                        extra={"project_id": project.id, "error": str(exc)[:200]},
                    )
                    values.update(
                        {
                            "resource_status": "unavailable",
                            "cpu_percent": 0.0,
                            "memory_percent": 0.0,
                            "memory_used": 0,
                            "memory_total": 0,
                            "network_bytes_sent": 0,
                            "network_bytes_received": 0,
                            "process_count": 0,
                        }
                    )
                if project.runtime_type in MINECRAFT_TYPES:
                    try:
                        values["minecraft"] = await asyncio.to_thread(
                            agent_operation,
                            "minecraft_status",
                            {
                                "project_id": project.id,
                                "game_port": int(
                                    project.runtime_config.get("game_port", 25565)
                                ),
                            },
                        )
                    except Exception:
                        values["minecraft"] = {
                            "available": False,
                            "online_players": None,
                            "max_players": None,
                        }
                resource_status = values.get("resource_status")
                active_operation = await session.scalar(
                    select(ProjectRuntime.active_job_id).where(
                        ProjectRuntime.project_id == project.id
                    )
                )
                if (
                    not active_operation
                    and project.status != "deploying"
                    and resource_status in {"running", "stopped"}
                ):
                    project.status = str(resource_status)
                violations = _resource_violations(values, policy)
                values["resource_violations"] = violations
                resource_alert = await session.scalar(
                    select(Notification)
                    .where(
                        Notification.notification_type == "project.resource_limit_exceeded",
                        Notification.resource_id == project.id,
                        Notification.read_at.is_(None),
                    )
                    .order_by(Notification.created_at.desc())
                )
                if violations:
                    if resource_alert is None:
                        resources = ", ".join(
                            str(violation["resource"]) for violation in violations
                        )
                        message = (
                            f"Проект {project.name} превысил resource policy: {resources}."
                        )
                        session.add(
                            Notification(
                                notification_type="project.resource_limit_exceeded",
                                severity="warning",
                                title="Превышен лимит ресурсов проекта",
                                message=message,
                                resource_type="project",
                                resource_id=project.id,
                            )
                        )
                        deliveries.append(("Превышен лимит ресурсов проекта", message))
                elif resource_alert is not None:
                    resource_alert.read_at = now
                health_failed = False
                if project.healthcheck_url:
                    checked += 1
                    try:
                        response = await client.get(project.healthcheck_url)
                        values["health_status_code"] = response.status_code
                        if 200 <= response.status_code < 400:
                            values["health_status"] = "ok"
                        else:
                            values["health_status"] = "failed"
                            health_failed = True
                    except Exception as exc:
                        values["health_status"] = "failed"
                        values["health_error"] = str(exc)[:500]
                        health_failed = True
                    existing = await session.scalar(
                        select(Notification)
                        .where(
                            Notification.notification_type == "project.down",
                            Notification.resource_id == project.id,
                            Notification.read_at.is_(None),
                        )
                        .order_by(Notification.created_at.desc())
                    )
                    if health_failed:
                        failed += 1
                        if existing is None:
                            message = f"Healthcheck проекта {project.name} не проходит."
                            session.add(
                                Notification(
                                    notification_type="project.down",
                                    severity="error",
                                    title="Проект недоступен",
                                    message=message,
                                    resource_type="project",
                                    resource_id=project.id,
                                )
                            )
                            deliveries.append(("Проект недоступен", message))
                    elif existing is not None:
                        existing.read_at = now
                session.add(MetricSample(project_id=project.id, captured_at=now, values=values))
        await session.commit()
    for title, message in deliveries:
        enqueue_telegram_alert(title, message)
    return {"projects": len(projects), "health_checked": checked, "health_failed": failed}


async def store_monitoring_metrics(values: dict[str, Any]) -> dict[str, int]:
    """Persist host and project monitoring data on one event loop."""
    await store_host_metrics(values)
    return await store_project_metrics()


@celery_app.task(name="monitoring.collect")
def collect_host_metrics() -> dict[str, Any]:
    """Collect host metrics through the system agent and persist alerts."""
    values = agent_operation("host_metrics")
    project_result = asyncio.run(store_monitoring_metrics(values))
    return {"host": values, "projects": project_result}


@celery_app.task(name="domains.renew_certificates")
def renew_certificates() -> dict[str, Any]:
    """Run scheduled Certbot renewal and reload validated Nginx."""
    result = agent_operation("renew_certificates", timeout=630)
    agent_operation("validate_nginx")
    agent_operation("reload_nginx")
    refreshed = asyncio.run(_refresh_certificate_metadata())
    return {**result, "refreshed": refreshed}


async def _refresh_certificate_metadata() -> int:
    """Refresh persisted metadata for all active managed certificates."""
    async with SessionFactory() as session:
        domains = list(
            await session.scalars(select(Domain).where(Domain.ssl_status == "active"))
        )
    refreshed = 0
    for domain in domains:
        metadata = await asyncio.to_thread(
            agent_operation,
            "certificate_info",
            {"hostname": domain.hostname},
        )
        await _store_domain_certificate(domain.id, "active", metadata)
        refreshed += 1
    return refreshed


@celery_app.task(bind=True, name="domains.renew_certificate")
def renew_certificate(
    task: Any,
    *,
    domain_id: str,
    hostname: str,
) -> dict[str, Any]:
    """Force renewal of one confirmed domain and persist certificate metadata."""
    task.update_state(state="PROGRESS", meta={"progress": 10, "stage": "certificate"})
    try:
        result = agent_operation(
            "renew_certificate",
            {"hostname": hostname, "force": True},
            timeout=630,
        )
        agent_operation("validate_nginx")
        agent_operation("reload_nginx")
        metadata = agent_operation("certificate_info", {"hostname": hostname})
        asyncio.run(_store_domain_certificate(domain_id, "active", metadata))
    except Exception:
        asyncio.run(_store_domain_certificate(domain_id, "failed", None))
        raise
    task.update_state(state="PROGRESS", meta={"progress": 100, "stage": "complete"})
    return {
        "domain_id": domain_id,
        "hostname": hostname,
        "ssl_status": "active",
        "renewal_output": str(result.get("stdout", ""))[-4096:],
        **metadata,
    }


async def _cron_agent_request(
    project: Project,
    job: CronJob,
    session: Any,
) -> tuple[str, dict[str, Any]]:
    """Build the allowlisted agent operation for a project cron job."""
    if project.runtime_type == "docker":
        return (
            "container_command",
            {
                "container": f"vps-project-{project.id}",
                "command": job.command,
            },
        )
    if project.runtime_type == "docker_compose":
        service = str(
            project.runtime_config.get("cron_service")
            or project.runtime_config.get("service")
            or "app"
        )
        return (
            "compose_command",
            {
                "project_id": project.id,
                "service": service,
                "command": job.command,
            },
        )
    if project.runtime_type in {"systemd", "pm2"}:
        return (
            "run_project_command",
            {
                "project_id": project.id,
                "command": job.command,
            },
        )
    if project.runtime_type in MINECRAFT_TYPES:
        active_job = await session.scalar(
            select(ProjectRuntime.active_job_id)
            .where(ProjectRuntime.project_id == project.id)
            .with_for_update()
        )
        if active_job:
            raise ValueError("Minecraft operation in progress")
        configuration = project.runtime_config
        if not bool(configuration.get("rcon_enabled", False)):
            raise ValueError("Minecraft RCON is disabled")
        variable = await session.scalar(
            select(EnvironmentVariable).where(
                EnvironmentVariable.project_id == project.id,
                EnvironmentVariable.key == "MINECRAFT_RCON_PASSWORD",
            )
        )
        if variable is None:
            raise ValueError("Minecraft RCON password is not configured")
        password = SecretCipher(settings.field_encryption_key).decrypt(variable.encrypted_value)
        return (
            "minecraft_command",
            {
                "project_id": project.id,
                "rcon_host_port": int(configuration.get("rcon_host_port", 25575)),
                "rcon_password": password,
                "command": job.command,
            },
        )
    raise ValueError("Unsupported cron runtime")


async def execute_due_cron_jobs() -> dict[str, int]:
    """Execute due project cron commands and persist their result."""
    now = datetime.now(UTC)
    executed = 0
    failed = 0
    async with SessionFactory() as session:
        jobs = await session.scalars(select(CronJob).where(CronJob.enabled.is_(True)))
        for job in jobs:
            base = job.last_run_at or job.created_at
            if base.tzinfo is None:
                base = base.replace(tzinfo=UTC)
            next_run = croniter(job.expression, base).get_next(datetime)
            if next_run > now:
                continue
            project = await session.get(Project, job.project_id)
            if project is None:
                job.last_status = "unsupported"
                job.last_run_at = now
                failed += 1
                continue
            try:
                operation, parameters = await _cron_agent_request(project, job, session)
                agent_operation(operation, parameters, timeout=150)
                job.last_status = "success"
                executed += 1
            except ValueError:
                job.last_status = "unsupported"
                failed += 1
            except Exception:
                job.last_status = "failed"
                failed += 1
            job.last_run_at = now
        await session.commit()
    return {"executed": executed, "failed": failed}


@celery_app.task(name="cron.tick")
def run_due_cron_jobs() -> dict[str, int]:
    """Run the periodic project cron scheduler tick."""
    return asyncio.run(execute_due_cron_jobs())


async def schedule_due_backups() -> dict[str, int]:
    """Create jobs for due backup policies and advance their schedule."""
    now = datetime.now(UTC)
    queued_tasks: list[tuple[str, str, dict[str, Any]]] = []
    deliveries: list[tuple[str, str]] = []
    async with SessionFactory() as session:
        policies = await session.scalars(
            select(BackupPolicy).where(BackupPolicy.enabled.is_(True))
        )
        for policy in policies:
            base = policy.last_run_at or policy.created_at
            if base.tzinfo is None:
                base = base.replace(tzinfo=UTC)
            if croniter(policy.schedule, base).get_next(datetime) > now:
                continue
            project = await session.get(Project, policy.project_id)
            policy.last_run_at = now
            if project is None:
                continue
            if project.runtime_type in MINECRAFT_TYPES:
                runtime = await session.scalar(
                    select(ProjectRuntime)
                    .where(ProjectRuntime.project_id == project.id)
                    .with_for_update()
                )
                if runtime is None or runtime.active_job_id:
                    continue
            source = runtime_root(settings.storage_root, project)
            if source.is_dir():
                backup = Backup(
                    project_id=project.id,
                    backup_type="project",
                    status="queued",
                )
                session.add(backup)
                await session.flush()
                job = Job(
                    kind="backup.create_project",
                    payload={
                        "backup_id": backup.id,
                        "project_id": project.id,
                        "scheduled": True,
                    },
                )
                session.add(job)
                await session.flush()
                await reserve_minecraft_job(session, project, job)
                queued_tasks.append(
                    (
                        "backups.create_project",
                        job.id,
                        {
                            "backup_id": backup.id,
                            "source": str(source),
                            "backup_root": str(settings.storage_root / "backups"),
                            "project_id": project.id,
                            "include_items": policy.include_items,
                            "encryption_enabled": policy.encryption_enabled,
                            "retention_count": policy.retention_count,
                        },
                    )
                )
            else:
                message = f"Файлы проекта {project.name} не найдены."
                session.add(
                    Notification(
                        notification_type="backup.source_missing",
                        severity="error",
                        title="Не удалось запустить бэкап",
                        message=message,
                        resource_type="project",
                        resource_id=project.id,
                    )
                )
                deliveries.append(("Не удалось запустить бэкап", message))
            if policy.include_databases:
                databases = await session.scalars(
                    select(DatabaseInstance).where(
                        DatabaseInstance.project_id == project.id,
                        DatabaseInstance.status == "ready",
                    )
                )
                for database in databases:
                    backup = Backup(
                        database_id=database.id,
                        backup_type="database",
                        status="queued",
                    )
                    session.add(backup)
                    await session.flush()
                    job = Job(
                        kind="database.export",
                        payload={
                            "backup_id": backup.id,
                            "database_id": database.id,
                            "scheduled": True,
                        },
                    )
                    session.add(job)
                    await session.flush()
                    queued_tasks.append(
                        (
                            "databases.export",
                            job.id,
                            {
                                "database_id": database.id,
                                "backup_id": backup.id,
                                "retention_count": policy.retention_count,
                            },
                        )
                    )
        await session.commit()
    for title, message in deliveries:
        enqueue_telegram_alert(title, message)
    for task_name, task_id, kwargs in queued_tasks:
        celery_app.send_task(task_name, task_id=task_id, kwargs=kwargs)
    return {"queued": len(queued_tasks)}


@celery_app.task(name="backups.tick")
def run_due_backup_policies() -> dict[str, int]:
    """Run the periodic backup policy scheduler tick."""
    return asyncio.run(schedule_due_backups())
