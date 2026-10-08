"""Minecraft Forge project configuration and lifecycle endpoints."""

import json
import os
import re
import secrets
import tempfile
from pathlib import Path

from fastapi import APIRouter, HTTPException, UploadFile
from sqlalchemy import select

from app.api.dependencies import AppSettings, CurrentUser, DbSession
from app.core.security import SecretCipher
from app.models.entities import (
    BUKKIT_TYPES,
    MINECRAFT_TYPES,
    EnvironmentVariable,
    Job,
    Project,
    ProjectRuntime,
)
from app.schemas.common import MessageResponse
from app.schemas.jobs import JobResponse
from app.schemas.minecraft import (
    MinecraftBuildCatalog,
    MinecraftConfigResponse,
    MinecraftConfigUpdate,
    MinecraftInstallRequest,
    MinecraftModEntry,
    MinecraftPlayerAction,
    MinecraftPluginAction,
    MinecraftPluginEntry,
    MinecraftTextFileResponse,
    MinecraftTextFileUpdate,
    MinecraftVersionCatalog,
)
from app.services.agent import AgentClient
from app.services.audit import write_audit_log
from app.services.minecraft import (
    RCON_ENV_KEY,
    MinecraftCatalogError,
    forge_version_catalog,
    minecraft_parameters,
    recommended_java_version,
    start_minecraft_runtime,
)
from app.services.minecraft_catalogs import (
    java_for_engine,
    resolve_artifact,
    server_builds,
    server_versions,
)
from app.services.minecraft_operations import (
    assert_minecraft_idle,
    minecraft_action,
    release_minecraft_job,
    reserve_minecraft_job,
)
from app.services.paths import UnsafePathError, resolve_within, runtime_root
from app.services.permissions import require_project_permission
from app.services.queue import enqueue

router = APIRouter(prefix="/projects/{project_id}/minecraft", tags=["minecraft"])
EDITABLE_TEXT_FILES = {"server.properties", "whitelist.json", "ops.json", "banned-players.json"}
BUKKIT_TEXT_FILES = {"bukkit.yml", "spigot.yml", "commands.yml", "permissions.yml"}
PAPER_TEXT_FILES = {"paper.yml", "config/paper-global.yml", "config/paper-world-defaults.yml"}
MANAGED_PROPERTIES = {
    "server-port",
    "enable-rcon",
    "rcon.port",
    "rcon.password",
    "broadcast-rcon-to-ops",
}


async def _minecraft_project(session: DbSession, project_id: str) -> Project:
    """Load a Minecraft Forge project."""
    project = await session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    if project.project_type not in MINECRAFT_TYPES:
        raise HTTPException(status_code=409, detail="Project is not Minecraft")
    return project


async def _minecraft_root(
    session: DbSession,
    settings: AppSettings,
    project_id: str,
) -> tuple[Project, Path]:
    """Return a Minecraft project and its filesystem root."""
    project = await _minecraft_project(session, project_id)
    try:
        root = runtime_root(settings.storage_root, project)
    except UnsafePathError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    root.mkdir(parents=True, exist_ok=True)
    return project, root


def _editable_text_path(root: Path, filename: str, project: Project) -> Path:
    """Resolve one allowlisted Minecraft text file path."""
    allowed = set(EDITABLE_TEXT_FILES)
    if project.project_type in BUKKIT_TYPES:
        allowed.update(BUKKIT_TEXT_FILES)
    if project.project_type in {"minecraft_paper", "minecraft_purpur"}:
        allowed.update(PAPER_TEXT_FILES)
    if project.project_type == "minecraft_purpur":
        allowed.add("purpur.yml")
    if filename not in allowed:
        raise HTTPException(status_code=404, detail="Editable Minecraft file not found")
    try:
        current = root
        for part in Path(filename).parts:
            current /= part
            if current.is_symlink():
                raise UnsafePathError("Minecraft configuration cannot be a symlink")
        if current.exists() and not current.is_file():
            raise UnsafePathError("Minecraft configuration must be a regular file")
        return resolve_within(root, filename)
    except UnsafePathError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _validate_minecraft_text(filename: str, content: str) -> None:
    """Validate Minecraft text file content before writing it."""
    if "\x00" in content:
        raise HTTPException(status_code=422, detail="File contains NUL byte")
    if filename == "server.properties":
        for line in content.splitlines():
            if not line.strip() or line.lstrip().startswith(("#", "!")):
                continue
            if (
                not re.fullmatch(r"[A-Za-z0-9_.-]+", line.split("=", 1)[0].strip())
                or "=" not in line
                or (len(line) - len(line.rstrip("\\"))) % 2
            ):
                raise HTTPException(
                    status_code=422, detail="Properties require plain key=value lines"
                )
    if filename in {"whitelist.json", "ops.json", "banned-players.json"}:
        try:
            payload = json.loads(content or "[]")
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=422, detail="File must be valid JSON") from exc
        if not isinstance(payload, list):
            raise HTTPException(status_code=422, detail="Minecraft list file must be an array")


@router.get("", response_model=MinecraftConfigResponse)
async def get_minecraft_config(
    project_id: str,
    user: CurrentUser,
    session: DbSession,
) -> MinecraftConfigResponse:
    """Return Minecraft settings without the RCON password."""
    await require_project_permission(session, user, project_id, "project:read")
    project = await _minecraft_project(session, project_id)
    password_exists = await session.scalar(
        select(EnvironmentVariable.id).where(
            EnvironmentVariable.project_id == project.id,
            EnvironmentVariable.key == RCON_ENV_KEY,
        )
    )
    return MinecraftConfigResponse(
        configuration={
            **(
                {"java_version": 25, "server_jar": "server.jar", "launch_mode": "jar"}
                if project.project_type in BUKKIT_TYPES
                else {}
            ),
            **project.runtime_config,
        },
        engine=project.project_type.removeprefix("minecraft_"),
        status=project.status,
        active_job_id=await session.scalar(
            select(ProjectRuntime.active_job_id).where(ProjectRuntime.project_id == project.id)
        ),
        rcon_configured=password_exists is not None,
    )


@router.put("", response_model=MinecraftConfigResponse)
async def configure_minecraft(
    project_id: str,
    data: MinecraftConfigUpdate,
    user: CurrentUser,
    session: DbSession,
    settings: AppSettings,
) -> MinecraftConfigResponse:
    """Save validated Forge settings and encrypt the RCON password."""
    await require_project_permission(session, user, project_id, "project:update")
    project = await _minecraft_project(session, project_id)
    await assert_minecraft_idle(session, project)
    if project.project_type in BUKKIT_TYPES:
        if data.rcon_password and any(
            ord(char) < 33 or ord(char) > 126 or char == "\\" for char in data.rcon_password
        ):
            raise HTTPException(
                status_code=422,
                detail="Use an ASCII RCON password without spaces or backslashes",
            )
        if project.status == "running":
            raise HTTPException(
                status_code=409, detail="Stop Minecraft before changing launch settings"
            )
        if data.forge_version or data.launch_mode != "jar" or data.server_jar != "server.jar":
            raise HTTPException(
                status_code=422, detail="Plugin servers require the managed server.jar launcher"
            )
        for key in ("minecraft_version", "build_id"):
            if getattr(data, key) != project.runtime_config.get(key):
                raise HTTPException(
                    status_code=409, detail="Use installation or update to change server builds"
                )
        version = project.runtime_config.get("minecraft_version")
        if version and data.java_version != java_for_engine(
            project.project_type.removeprefix("minecraft_"), str(version)
        ):
            raise HTTPException(status_code=422, detail="Java is incompatible with this server")
    elif (data.minecraft_version is None) != (data.forge_version is None):
        raise HTTPException(
            status_code=422, detail="Provide Minecraft and Forge versions together"
        )
    if data.rcon_enabled and (
        data.game_port == data.rcon_port or data.game_port == data.rcon_host_port
    ):
        raise HTTPException(status_code=422, detail="Game and RCON ports must differ")
    projects = await session.scalars(
        select(Project).where(
            Project.id != project.id, Project.runtime_type.in_(MINECRAFT_TYPES)
        )
    )
    requested_ports = {data.game_port, *({data.rcon_host_port} if data.rcon_enabled else set())}
    for other in projects:
        cfg = other.runtime_config
        reserved_ports = {
            cfg.get("game_port", 25565),
            *({cfg.get("rcon_host_port", 25575)} if cfg.get("rcon_enabled") else set()),
        }
        if requested_ports & reserved_ports:
            raise HTTPException(
                status_code=409, detail="Minecraft port is reserved by another project"
            )
    if (
        data.rcon_enabled
        and settings.environment not in {"development", "test"}
        and not settings.field_encryption_key
    ):
        raise HTTPException(
            status_code=409,
            detail="FIELD_ENCRYPTION_KEY is required for RCON",
        )
    minimum = int(data.xms[:-1]) * (1024 if data.xms[-1].upper() == "G" else 1)
    maximum = int(data.xmx[:-1]) * (1024 if data.xmx[-1].upper() == "G" else 1)
    if minimum > maximum or maximum > 65536:
        raise HTTPException(status_code=422, detail="Invalid memory range")
    existing = await session.scalar(
        select(EnvironmentVariable).where(
            EnvironmentVariable.project_id == project.id,
            EnvironmentVariable.key == RCON_ENV_KEY,
        )
    )
    one_time_password = None
    if data.rcon_enabled and (data.rcon_password or existing is None):
        password = data.rcon_password or secrets.token_urlsafe(32)
        encrypted = SecretCipher(settings.field_encryption_key).encrypt(password)
        if existing is None:
            existing = EnvironmentVariable(
                project_id=project.id,
                key=RCON_ENV_KEY,
                encrypted_value=encrypted,
                is_secret=True,
            )
            session.add(existing)
        else:
            existing.encrypted_value = encrypted
        one_time_password = password
    configuration = {**project.runtime_config, **data.model_dump(exclude={"rcon_password"})}
    project.runtime_config = configuration
    runtime = await session.scalar(
        select(ProjectRuntime).where(ProjectRuntime.project_id == project.id)
    )
    if runtime is not None:
        runtime.runtime_type = project.runtime_type
        runtime.configuration = configuration
    await write_audit_log(
        session,
        action="minecraft.configure",
        actor_id=user.id,
        resource_type="project",
        resource_id=project.id,
        details={
            "java_version": data.java_version,
            "xms": data.xms,
            "xmx": data.xmx,
            "game_port": data.game_port,
            "eula_accepted": data.eula_accepted,
        },
    )
    await session.commit()
    return MinecraftConfigResponse(
        configuration=configuration,
        rcon_configured=data.rcon_enabled,
        one_time_rcon_password=one_time_password,
        engine=project.project_type.removeprefix("minecraft_"),
        status=project.status,
    )


@router.post("/start", response_model=MessageResponse)
async def start_minecraft(
    project_id: str,
    user: CurrentUser,
    session: DbSession,
    settings: AppSettings,
) -> MessageResponse:
    """Start the configured Forge server container."""
    await require_project_permission(session, user, project_id, "runtime:control")
    project = await _minecraft_project(session, project_id)
    try:
        async with minecraft_action(session, project, "start"):
            await start_minecraft_runtime(project, session, settings)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail="Minecraft start failed") from exc
    project.status = "running"
    await write_audit_log(
        session,
        action="minecraft.start",
        actor_id=user.id,
        resource_type="project",
        resource_id=project.id,
    )
    await session.commit()
    return MessageResponse(message="Minecraft server started")


@router.post(
    "/install",
    response_model=JobResponse,
    status_code=202,
)
async def install_minecraft(
    project_id: str,
    data: MinecraftInstallRequest,
    user: CurrentUser,
    session: DbSession,
    settings: AppSettings,
) -> Job:
    """Queue an official or uploaded Forge installer through the system agent."""
    await require_project_permission(session, user, project_id, "deploy:write")
    project, root = await _minecraft_root(session, settings, project_id)
    if project.project_type in BUKKIT_TYPES:
        return await _queue_plugin_server_install(
            project, data, session, settings, user.id, updating=False
        )
    if data.build_id or data.uploaded_jar:
        raise HTTPException(status_code=422, detail="Forge requires a Forge installer")
    minecraft_version = data.minecraft_version
    forge_version = data.forge_version
    if minecraft_version is not None and forge_version is not None:
        try:
            catalog = await forge_version_catalog()
        except MinecraftCatalogError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        selected = next(
            (item for item in catalog if item["minecraft_version"] == minecraft_version),
            None,
        )
        forge_versions = selected.get("forge_versions") if selected else None
        if not isinstance(forge_versions, list) or forge_version not in forge_versions:
            raise HTTPException(status_code=422, detail="Unsupported Forge version pair")
        java_version = recommended_java_version(minecraft_version)
    else:
        assert data.installer_jar is not None
        try:
            installer_path = resolve_within(root, data.installer_jar)
        except UnsafePathError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if not installer_path.is_file() or installer_path.is_symlink():
            raise HTTPException(
                status_code=409,
                detail="Forge installer JAR was not uploaded",
            )
        java_version = int(project.runtime_config.get("java_version", 21))
    job = Job(
        kind="minecraft.install",
        payload={
            "project_id": project.id,
            "installer_jar": data.installer_jar,
            "minecraft_version": minecraft_version,
            "forge_version": forge_version,
            "java_version": java_version,
        },
    )
    session.add(job)
    await reserve_minecraft_job(session, project, job)
    await write_audit_log(
        session,
        action="minecraft.install",
        actor_id=user.id,
        resource_type="project",
        resource_id=project.id,
        details={
            "job_id": job.id,
            "installer_jar": data.installer_jar,
            "minecraft_version": minecraft_version,
            "forge_version": forge_version,
        },
    )
    await session.commit()
    enqueue(
        "minecraft.install",
        task_id=job.id,
        kwargs={
            "project_id": project.id,
            "java_version": java_version,
            "installer_jar": data.installer_jar,
            "minecraft_version": minecraft_version,
            "forge_version": forge_version,
        },
    )
    return job


@router.get("/versions", response_model=MinecraftVersionCatalog)
async def minecraft_versions(
    project_id: str,
    user: CurrentUser,
    session: DbSession,
) -> MinecraftVersionCatalog:
    """Return official Forge builds grouped by Minecraft version."""
    await require_project_permission(session, user, project_id, "project:read")
    project = await _minecraft_project(session, project_id)
    try:
        versions = (
            await server_versions(project.project_type.removeprefix("minecraft_"))
            if project.project_type in BUKKIT_TYPES
            else await forge_version_catalog()
        )
    except MinecraftCatalogError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except (KeyError, TypeError, AttributeError) as exc:
        raise HTTPException(
            status_code=502, detail="Invalid official Minecraft catalog"
        ) from exc
    return MinecraftVersionCatalog(versions=versions)


@router.get("/files/{filename:path}", response_model=MinecraftTextFileResponse)
async def read_minecraft_text_file(
    project_id: str,
    filename: str,
    user: CurrentUser,
    session: DbSession,
    settings: AppSettings,
) -> MinecraftTextFileResponse:
    """Read an allowlisted Minecraft text configuration file."""
    await require_project_permission(session, user, project_id, "files:read")
    project, root = await _minecraft_root(session, settings, project_id)
    path = _editable_text_path(root, filename, project)
    if not path.exists():
        default = "[]\n" if filename in {"whitelist.json", "ops.json"} else ""
        return MinecraftTextFileResponse(path=filename, content=default)
    if not path.is_file() or path.is_symlink() or path.stat().st_size > 2 * 1024 * 1024:
        raise HTTPException(status_code=422, detail="Minecraft file is not editable")
    try:
        content = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=415, detail="Minecraft file is not UTF-8") from exc
    if filename == "server.properties":
        content = _mask_properties(content)
    return MinecraftTextFileResponse(path=filename, content=content)


@router.put("/files/{filename:path}", response_model=MinecraftTextFileResponse)
async def write_minecraft_text_file(
    project_id: str,
    filename: str,
    data: MinecraftTextFileUpdate,
    user: CurrentUser,
    session: DbSession,
    settings: AppSettings,
) -> MinecraftTextFileResponse:
    """Replace an allowlisted Minecraft text configuration file."""
    await require_project_permission(session, user, project_id, "files:write")
    project, root = await _minecraft_root(session, settings, project_id)
    await assert_minecraft_idle(session, project)
    path = _editable_text_path(root, filename, project)
    _validate_minecraft_text(filename, data.content)
    content = data.content
    if filename == "server.properties":
        existing = path.read_text(encoding="utf-8") if path.exists() else ""
        retained = [
            line
            for line in existing.splitlines()
            if line.split("=", 1)[0].strip() in MANAGED_PROPERTIES
        ]
        content = (
            "\n".join(
                [
                    line
                    for line in content.splitlines()
                    if line.split("=", 1)[0].strip() not in MANAGED_PROPERTIES
                ]
                + retained
            )
            + "\n"
        )
    _atomic_text(path, content)
    await write_audit_log(
        session,
        action="minecraft.file_edit",
        actor_id=user.id,
        resource_type="project",
        resource_id=project.id,
        details={"path": filename, "size_bytes": len(data.content.encode("utf-8"))},
    )
    await session.commit()
    return MinecraftTextFileResponse(
        path=filename,
        content=_mask_properties(content) if filename == "server.properties" else content,
    )


@router.get("/mods", response_model=list[MinecraftModEntry])
async def list_minecraft_mods(
    project_id: str,
    user: CurrentUser,
    session: DbSession,
    settings: AppSettings,
) -> list[MinecraftModEntry]:
    """List installed mod JAR files."""
    await require_project_permission(session, user, project_id, "files:read")
    _, root = await _minecraft_root(session, settings, project_id)
    mods = resolve_within(root, "mods")
    if not mods.exists():
        return []
    if not mods.is_dir() or mods.is_symlink():
        raise HTTPException(status_code=422, detail="Mods path is not a directory")
    entries = [
        MinecraftModEntry(
            name=item.name,
            path=item.relative_to(root).as_posix(),
            size_bytes=item.stat().st_size,
        )
        for item in sorted(mods.iterdir(), key=lambda path: path.name.lower())
        if item.is_file() and not item.is_symlink() and item.suffix.lower() == ".jar"
    ]
    return entries


def _mask_properties(content: str) -> str:
    """Remove RCON secrets from normal configuration responses."""
    retained = []
    logical = ""
    for line in content.splitlines():
        logical += line.lstrip() if logical else line
        trailing = len(logical) - len(logical.rstrip("\\"))
        if trailing % 2:
            logical = logical[:-1]
            continue
        decoded = re.sub(r"\\u([A-Fa-f0-9]{4})", lambda match: chr(int(match[1], 16)), logical)
        decoded = re.sub(r"\\(.)", r"\1", decoded)
        key = re.split(r"[=:\s]", decoded.lstrip(), maxsplit=1)[0]
        if key != "rcon.password":
            retained.append(logical)
        logical = ""
    return "\n".join(retained) + "\n"


def _atomic_text(path: Path, content: str) -> None:
    """Replace configuration using a private file on the same filesystem."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o640)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


async def _queue_plugin_server_install(
    project: Project,
    data: MinecraftInstallRequest,
    session: DbSession,
    settings: AppSettings,
    actor_id: str,
    *,
    updating: bool,
) -> Job:
    """Resolve an official or uploaded plugin server and reserve its durable job."""
    if data.forge_version or data.installer_jar:
        raise HTTPException(
            status_code=422, detail="Plugin servers do not use Forge installers"
        )
    if not data.minecraft_version:
        raise HTTPException(status_code=422, detail="Declare the server's Minecraft version")
    await assert_minecraft_idle(session, project)
    installed = bool(project.runtime_config.get("sha256"))
    if project.runtime_config.get("eula_accepted") is not True:
        raise HTTPException(
            status_code=422,
            detail="Accept the Minecraft EULA and save configuration before installation",
        )
    try:
        await minecraft_parameters(project, session, settings)
    except ValueError as exc:
        raise HTTPException(
            status_code=422, detail="Save valid Minecraft launch and RCON settings first"
        ) from exc
    root = runtime_root(settings.storage_root, project)
    if updating != installed:
        raise HTTPException(
            status_code=409,
            detail="Use update for an installed server, install for a new server",
        )
    if project.status == "running" and not updating:
        raise HTTPException(status_code=409, detail="Server must be stopped for installation")
    artifact = None
    engine = project.project_type.removeprefix("minecraft_")
    if data.uploaded_jar:
        try:
            source = resolve_within(root, data.uploaded_jar)
            if (root / data.uploaded_jar).is_symlink() or not source.is_file():
                raise ValueError("Uploaded server JAR does not exist")
        except (UnsafePathError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    else:
        if not data.minecraft_version or not data.build_id:
            raise HTTPException(status_code=422, detail="Select a Minecraft version and build")
        try:
            artifact = await resolve_artifact(engine, data.minecraft_version, data.build_id)
        except MinecraftCatalogError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        except (ValueError, KeyError, TypeError) as exc:
            raise HTTPException(status_code=422, detail="Unsupported official build") from exc
    previous_version = project.runtime_config.get("minecraft_version")
    if updating and previous_version:
        if data.minecraft_version is None or tuple(
            map(int, data.minecraft_version.split("."))
        ) < tuple(map(int, str(previous_version).split("."))):
            raise HTTPException(status_code=409, detail="Downgrade requires restoring a backup")
    job = Job(
        kind="minecraft.update" if updating else "minecraft.install",
        payload={
            "project_id": project.id,
            "engine": engine,
            "artifact": artifact,
            "uploaded_jar": data.uploaded_jar,
            "minecraft_version": data.minecraft_version,
            "java_version": artifact["java_version"]
            if artifact
            else java_for_engine(engine, data.minecraft_version)
            if data.minecraft_version
            else int(project.runtime_config.get("java_version", 21)),
            "updating": updating,
            "phase": "queued",
        },
    )
    await reserve_minecraft_job(session, project, job)
    await write_audit_log(
        session,
        action=job.kind,
        actor_id=actor_id,
        resource_type="project",
        resource_id=project.id,
        details={
            "job_id": job.id,
            "build_id": data.build_id,
            "minecraft_version": data.minecraft_version,
        },
    )
    await session.commit()
    try:
        enqueue("minecraft.provision", task_id=job.id, kwargs={"project_id": project.id})
    except Exception:
        job.status = "failure"
        job.error = "Minecraft job could not be queued"
        await release_minecraft_job(session, project.id, job.id)
        await session.commit()
        raise HTTPException(status_code=503, detail=job.error) from None
    return job


@router.get("/versions/{version}/builds", response_model=MinecraftBuildCatalog)
async def minecraft_builds(
    project_id: str, version: str, user: CurrentUser, session: DbSession
) -> MinecraftBuildCatalog:
    """Return the selected release's source-specific build catalog."""
    await require_project_permission(session, user, project_id, "project:read")
    project = await _minecraft_project(session, project_id)
    if project.project_type not in BUKKIT_TYPES:
        raise HTTPException(
            status_code=409, detail="Forge builds are in the existing version catalog"
        )
    try:
        return MinecraftBuildCatalog(
            builds=await server_builds(project.project_type.removeprefix("minecraft_"), version)
        )
    except MinecraftCatalogError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except (ValueError, KeyError, TypeError) as exc:
        raise HTTPException(status_code=422, detail="Unsupported Minecraft release") from exc


@router.post("/update", response_model=JobResponse, status_code=202)
async def update_minecraft(
    project_id: str,
    data: MinecraftInstallRequest,
    user: CurrentUser,
    session: DbSession,
    settings: AppSettings,
) -> Job:
    """Queue a manually selected update with a mandatory complete backup."""
    await require_project_permission(session, user, project_id, "deploy:write")
    project = await _minecraft_project(session, project_id)
    if project.project_type not in BUKKIT_TYPES:
        raise HTTPException(status_code=409, detail="Use the existing Forge installation flow")
    return await _queue_plugin_server_install(
        project, data, session, settings, user.id, updating=True
    )


@router.get("/plugins", response_model=list[MinecraftPluginEntry])
async def minecraft_plugins(
    project_id: str, user: CurrentUser, session: DbSession, settings: AppSettings
) -> list[MinecraftPluginEntry]:
    """List plugin JAR files without executing their code."""
    await require_project_permission(session, user, project_id, "files:read")
    project, _ = await _minecraft_root(session, settings, project_id)
    if project.project_type not in BUKKIT_TYPES:
        raise HTTPException(status_code=409, detail="Forge servers use mods")
    result = await AgentClient(settings).execute(
        "minecraft_plugin", {"project_id": project.id, "action": "list"}
    )
    return [MinecraftPluginEntry.model_validate(item) for item in result["plugins"]]


async def _plugin_mutation(
    project: Project,
    parameters: dict[str, object],
    user: CurrentUser,
    session: DbSession,
    settings: AppSettings,
) -> None:
    """Reserve stopped-server plugin changes and audit a sanitized result."""
    if project.project_type not in BUKKIT_TYPES:
        raise HTTPException(status_code=409, detail="Forge servers use mods")
    async with minecraft_action(session, project, "plugin"):
        try:
            await AgentClient(settings).execute(
                "minecraft_plugin",
                {
                    "project_id": project.id,
                    "engine": project.project_type.removeprefix("minecraft_"),
                    **parameters,
                },
                request_timeout=120,
            )
        except Exception as exc:
            raise HTTPException(
                status_code=409,
                detail="Plugin change failed; stop the server and check the JAR",
            ) from exc
    await write_audit_log(
        session,
        action="minecraft.plugin",
        actor_id=user.id,
        resource_type="project",
        resource_id=project.id,
        details={"name": parameters.get("name"), "action": parameters["action"]},
    )
    await session.commit()


@router.post("/plugins", response_model=MessageResponse)
async def upload_minecraft_plugin(
    project_id: str,
    file: UploadFile,
    user: CurrentUser,
    session: DbSession,
    settings: AppSettings,
) -> MessageResponse:
    """Stream a bounded plugin JAR to agent-validated private staging."""
    from app.api.routes.files import _stage_upload

    await require_project_permission(session, user, project_id, "files:write")
    project = await _minecraft_project(session, project_id)
    name = file.filename or ""
    if (
        not name
        or len(name) > 255
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*\.jar", name)
    ):
        raise HTTPException(status_code=422, detail="Invalid plugin filename")
    staged = None
    try:
        staged, _ = await _stage_upload(file, settings)
        await _plugin_mutation(
            project,
            {"action": "upload", "name": name, "staged_path": str(staged)},
            user,
            session,
            settings,
        )
    finally:
        if staged is not None:
            staged.unlink(missing_ok=True)
        await file.close()
    return MessageResponse(message="Plugin uploaded")


@router.post("/plugins/{name}", response_model=MessageResponse)
async def change_minecraft_plugin(
    project_id: str,
    name: str,
    data: MinecraftPluginAction,
    user: CurrentUser,
    session: DbSession,
    settings: AppSettings,
) -> MessageResponse:
    """Enable or disable a plugin on a stopped server."""
    await require_project_permission(session, user, project_id, "files:write")
    project = await _minecraft_project(session, project_id)
    await _plugin_mutation(
        project, {"action": data.action, "name": name}, user, session, settings
    )
    return MessageResponse(message="Plugin state changed")


@router.delete("/plugins/{name}", response_model=MessageResponse)
async def delete_minecraft_plugin(
    project_id: str, name: str, user: CurrentUser, session: DbSession, settings: AppSettings
) -> MessageResponse:
    """Delete only a plugin JAR while preserving its data directory."""
    await require_project_permission(session, user, project_id, "files:write")
    project = await _minecraft_project(session, project_id)
    await _plugin_mutation(project, {"action": "delete", "name": name}, user, session, settings)
    return MessageResponse(message="Plugin deleted")


@router.post("/players/actions", response_model=MessageResponse)
async def minecraft_player_action(
    project_id: str,
    data: MinecraftPlayerAction,
    user: CurrentUser,
    session: DbSession,
    settings: AppSettings,
) -> MessageResponse:
    """Delegate validated player administration to the running Minecraft server."""
    from app.services.minecraft import minecraft_rcon_password

    await require_project_permission(session, user, project_id, "console:write")
    project = await _minecraft_project(session, project_id)
    await assert_minecraft_idle(session, project)
    if not project.runtime_config.get("rcon_enabled"):
        raise HTTPException(status_code=409, detail="Player actions require RCON")
    commands = {
        "whitelist_add": "whitelist add",
        "whitelist_remove": "whitelist remove",
        "op": "op",
        "deop": "deop",
        "ban": "ban",
        "pardon": "pardon",
        "kick": "kick",
    }
    command = f"{commands[data.action]} {data.player}"
    if data.reason and data.action in {"ban", "kick"}:
        command += f" {data.reason}"
    try:
        result = await AgentClient(settings).execute(
            "minecraft_command",
            {
                "project_id": project.id,
                "rcon_host_port": int(project.runtime_config.get("rcon_host_port", 25575)),
                "rcon_password": await minecraft_rcon_password(session, project.id, settings),
                "command": command,
            },
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail="Minecraft RCON unavailable") from exc
    await write_audit_log(
        session,
        action="minecraft.player",
        actor_id=user.id,
        resource_type="project",
        resource_id=project.id,
        details={"action": data.action, "player": data.player},
    )
    await session.commit()
    return MessageResponse(message=str(result.get("stdout", "Command sent"))[:4096])


@router.post("/recovery", response_model=JobResponse, status_code=202)
async def retry_minecraft_recovery(
    project_id: str, user: CurrentUser, session: DbSession
) -> Job:
    """Retry a failed recovery without discarding its exclusive reservation."""
    await require_project_permission(session, user, project_id, "deploy:write")
    await _minecraft_project(session, project_id)
    job_id = await session.scalar(
        select(ProjectRuntime.active_job_id).where(ProjectRuntime.project_id == project_id)
    )
    job = await session.get(Job, job_id) if job_id else None
    if job is None or job.status != "failure":
        raise HTTPException(status_code=409, detail="No failed Minecraft recovery to retry")
    job.status = "started"
    await session.commit()
    try:
        enqueue("minecraft.recover_one", task_id=job.id, kwargs={"project_id": project_id})
    except Exception:
        job.status = "failure"
        await session.commit()
        raise HTTPException(
            status_code=503, detail="Minecraft recovery could not be queued"
        ) from None
    return job
