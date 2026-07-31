"""Minecraft Forge project configuration and lifecycle endpoints."""

import json
import secrets
from pathlib import Path

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from app.api.dependencies import AppSettings, CurrentUser, DbSession
from app.core.security import SecretCipher
from app.models.entities import EnvironmentVariable, Job, Project, ProjectRuntime
from app.schemas.common import MessageResponse
from app.schemas.jobs import JobResponse
from app.schemas.minecraft import (
    MinecraftConfigResponse,
    MinecraftConfigUpdate,
    MinecraftInstallRequest,
    MinecraftModEntry,
    MinecraftTextFileResponse,
    MinecraftTextFileUpdate,
    MinecraftVersionCatalog,
)
from app.services.audit import write_audit_log
from app.services.minecraft import (
    RCON_ENV_KEY,
    MinecraftCatalogError,
    forge_version_catalog,
    recommended_java_version,
    start_minecraft_runtime,
)
from app.services.paths import UnsafePathError, resolve_within, runtime_root
from app.services.queue import enqueue

router = APIRouter(prefix="/projects/{project_id}/minecraft", tags=["minecraft"])
EDITABLE_TEXT_FILES = {"server.properties", "whitelist.json", "ops.json"}


async def _minecraft_project(session: DbSession, project_id: str) -> Project:
    """Load a Minecraft Forge project."""
    project = await session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    if project.project_type != "minecraft_forge":
        raise HTTPException(status_code=409, detail="Project is not Minecraft Forge")
    return project


async def _minecraft_root(
    session: DbSession,
    settings: AppSettings,
    project_id: str,
) -> tuple[Project, Path]:
    """Return a Minecraft project and its filesystem root."""
    project = await _minecraft_project(session, project_id)
    root = runtime_root(settings.storage_root, project)
    root.mkdir(parents=True, exist_ok=True)
    return project, root


def _editable_text_path(root: Path, filename: str) -> Path:
    """Resolve one allowlisted Minecraft text file path."""
    if filename not in EDITABLE_TEXT_FILES:
        raise HTTPException(status_code=404, detail="Editable Minecraft file not found")
    try:
        return resolve_within(root, filename)
    except UnsafePathError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _validate_minecraft_text(filename: str, content: str) -> None:
    """Validate Minecraft text file content before writing it."""
    if "\x00" in content:
        raise HTTPException(status_code=422, detail="File contains NUL byte")
    if filename in {"whitelist.json", "ops.json"}:
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
    del user
    project = await _minecraft_project(session, project_id)
    password_exists = await session.scalar(
        select(EnvironmentVariable.id).where(
            EnvironmentVariable.project_id == project.id,
            EnvironmentVariable.key == RCON_ENV_KEY,
        )
    )
    return MinecraftConfigResponse(
        configuration=project.runtime_config,
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
    project = await _minecraft_project(session, project_id)
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
    configuration = data.model_dump(exclude={"rcon_password"})
    project.runtime_config = configuration
    runtime = await session.scalar(
        select(ProjectRuntime).where(ProjectRuntime.project_id == project.id)
    )
    if runtime is not None:
        runtime.runtime_type = "minecraft_forge"
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
    )


@router.post("/start", response_model=MessageResponse)
async def start_minecraft(
    project_id: str,
    user: CurrentUser,
    session: DbSession,
    settings: AppSettings,
) -> MessageResponse:
    """Start the configured Forge server container."""
    project = await _minecraft_project(session, project_id)
    try:
        await start_minecraft_runtime(project, session, settings)
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
    project, root = await _minecraft_root(session, settings, project_id)
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
    del user
    await _minecraft_project(session, project_id)
    try:
        versions = await forge_version_catalog()
    except MinecraftCatalogError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return MinecraftVersionCatalog(versions=versions)


@router.get("/files/{filename}", response_model=MinecraftTextFileResponse)
async def read_minecraft_text_file(
    project_id: str,
    filename: str,
    user: CurrentUser,
    session: DbSession,
    settings: AppSettings,
) -> MinecraftTextFileResponse:
    """Read an allowlisted Minecraft text configuration file."""
    del user
    _, root = await _minecraft_root(session, settings, project_id)
    path = _editable_text_path(root, filename)
    if not path.exists():
        default = "[]\n" if filename in {"whitelist.json", "ops.json"} else ""
        return MinecraftTextFileResponse(path=filename, content=default)
    if not path.is_file() or path.is_symlink() or path.stat().st_size > 2 * 1024 * 1024:
        raise HTTPException(status_code=422, detail="Minecraft file is not editable")
    try:
        content = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=415, detail="Minecraft file is not UTF-8") from exc
    return MinecraftTextFileResponse(path=filename, content=content)


@router.put("/files/{filename}", response_model=MinecraftTextFileResponse)
async def write_minecraft_text_file(
    project_id: str,
    filename: str,
    data: MinecraftTextFileUpdate,
    user: CurrentUser,
    session: DbSession,
    settings: AppSettings,
) -> MinecraftTextFileResponse:
    """Replace an allowlisted Minecraft text configuration file."""
    project, root = await _minecraft_root(session, settings, project_id)
    path = _editable_text_path(root, filename)
    _validate_minecraft_text(filename, data.content)
    path.write_text(data.content, encoding="utf-8")
    path.chmod(0o640)
    await write_audit_log(
        session,
        action="minecraft.file_edit",
        actor_id=user.id,
        resource_type="project",
        resource_id=project.id,
        details={"path": filename, "size_bytes": len(data.content.encode("utf-8"))},
    )
    await session.commit()
    return MinecraftTextFileResponse(path=filename, content=data.content)


@router.get("/mods", response_model=list[MinecraftModEntry])
async def list_minecraft_mods(
    project_id: str,
    user: CurrentUser,
    session: DbSession,
    settings: AppSettings,
) -> list[MinecraftModEntry]:
    """List installed mod JAR files."""
    del user
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
