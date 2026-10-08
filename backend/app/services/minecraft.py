"""Minecraft catalogs, encrypted RCON access and runtime orchestration helpers."""

import re
import time

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.security import SecretCipher
from app.models.entities import (
    BUKKIT_TYPES,
    EnvironmentVariable,
    Project,
    ProjectResourcePolicy,
)
from app.services.agent import AgentClient

RCON_ENV_KEY = "MINECRAFT_RCON_PASSWORD"
FORGE_METADATA_URL = (
    "https://maven.minecraftforge.net/net/minecraftforge/forge/maven-metadata.xml"
)
FORGE_COORDINATE_PATTERN = re.compile(
    r"^(?P<minecraft>\d+(?:\.\d+){1,3})-(?P<forge>\d+(?:\.\d+){1,3})$"
)
CATALOG_TTL_SECONDS = 3600
CATALOG_MAX_BYTES = 2 * 1024 * 1024
_catalog_cached_at = 0.0
_catalog_cache: list[dict[str, object]] = []


class MinecraftCatalogError(RuntimeError):
    """Raised when the official Forge catalog cannot be loaded."""


def _version_key(value: str) -> tuple[int, ...]:
    """Return a numeric key for validated dotted version strings."""
    return tuple(int(part) for part in value.split("."))


def recommended_java_version(minecraft_version: str) -> int:
    """Return the Java major required by the selected Minecraft generation."""
    version = _version_key(minecraft_version)
    if version[0] > 1 or version >= (1, 20, 5):
        return 21
    if version >= (1, 18):
        return 17
    if version >= (1, 17):
        return 16
    return 8


def parse_forge_catalog(metadata: str) -> list[dict[str, object]]:
    """Parse stable Minecraft and Forge versions from official Maven metadata."""
    grouped: dict[str, set[str]] = {}
    for coordinate in re.findall(r"<version>\s*([^<]+?)\s*</version>", metadata):
        match = FORGE_COORDINATE_PATTERN.fullmatch(coordinate)
        if match is None:
            continue
        grouped.setdefault(match["minecraft"], set()).add(match["forge"])
    if not grouped:
        raise MinecraftCatalogError("Forge catalog contains no supported versions")
    return [
        {
            "minecraft_version": minecraft_version,
            "forge_versions": sorted(
                forge_versions,
                key=_version_key,
                reverse=True,
            ),
            "java_version": recommended_java_version(minecraft_version),
        }
        for minecraft_version, forge_versions in sorted(
            grouped.items(),
            key=lambda item: _version_key(item[0]),
            reverse=True,
        )
    ]


async def forge_version_catalog() -> list[dict[str, object]]:
    """Return a short-lived cached catalog from the official Forge Maven."""
    global _catalog_cache, _catalog_cached_at
    now = time.monotonic()
    if _catalog_cache and now - _catalog_cached_at < CATALOG_TTL_SECONDS:
        return _catalog_cache
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            response = await client.get(FORGE_METADATA_URL)
            response.raise_for_status()
    except httpx.HTTPError as exc:
        raise MinecraftCatalogError("Official Forge catalog is unavailable") from exc
    if len(response.content) > CATALOG_MAX_BYTES:
        raise MinecraftCatalogError("Forge catalog exceeds the size limit")
    catalog = parse_forge_catalog(response.text)
    _catalog_cache = catalog
    _catalog_cached_at = now
    return catalog


async def minecraft_rcon_password(
    session: AsyncSession,
    project_id: str,
    settings: Settings,
) -> str:
    """Load and decrypt the project RCON password."""
    variable = await session.scalar(
        select(EnvironmentVariable).where(
            EnvironmentVariable.project_id == project_id,
            EnvironmentVariable.key == RCON_ENV_KEY,
        )
    )
    if variable is None:
        raise ValueError("Minecraft RCON password is not configured")
    return SecretCipher(settings.field_encryption_key).decrypt(variable.encrypted_value)


async def minecraft_parameters(
    project: Project,
    session: AsyncSession,
    settings: Settings,
    *,
    require_rcon: bool = True,
) -> dict[str, object]:
    """Resolve validated launch settings and decrypt secrets only at execution."""
    configuration = project.runtime_config
    password = None
    if bool(configuration.get("rcon_enabled", False)):
        try:
            password = await minecraft_rcon_password(session, project.id, settings)
        except ValueError:
            if require_rcon:
                raise
    parameters = {
        "project_id": project.id,
        "java_version": int(configuration.get("java_version", 21)),
        "xms": str(configuration.get("xms", "1G")),
        "xmx": str(configuration.get("xmx", "4G")),
        "server_jar": str(
            configuration.get(
                "server_jar",
                "server.jar" if project.project_type in BUKKIT_TYPES else "forge-server.jar",
            )
        ),
        "launch_mode": str(configuration.get("launch_mode", "jar")),
        "game_port": int(configuration.get("game_port", 25565)),
        "eula_accepted": bool(configuration.get("eula_accepted", False)),
        "rcon_enabled": bool(configuration.get("rcon_enabled", False)),
        "rcon_port": int(configuration.get("rcon_port", 25575)),
        "rcon_host_port": int(configuration.get("rcon_host_port", 25575)),
        "rcon_password": password,
    }
    policy = await session.scalar(
        select(ProjectResourcePolicy).where(ProjectResourcePolicy.project_id == project.id)
    )
    if policy is not None and policy.enabled:
        parameters.update(cpu_cores=policy.cpu_cores, memory_mb=policy.memory_mb)
    parameters["wait_ready"] = project.project_type in BUKKIT_TYPES
    if project.project_type in BUKKIT_TYPES:
        parameters["server_sha256"] = configuration.get("sha256")
    return parameters


async def start_minecraft_runtime(
    project: Project, session: AsyncSession, settings: Settings
) -> dict[str, object]:
    """Start Minecraft with its persisted launcher, secret and resource policy."""
    if project.project_type in BUKKIT_TYPES and not project.runtime_config.get("sha256"):
        raise ValueError("Install Minecraft before starting the server")
    return await AgentClient(settings).execute(
        "start_minecraft",
        await minecraft_parameters(project, session, settings),
        request_timeout=390,
    )


async def control_minecraft_runtime(
    project: Project, action: str, session: AsyncSession, settings: Settings
) -> None:
    """Apply Minecraft lifecycle actions consistently for UI and Telegram."""
    if action not in {"start", "stop", "restart"}:
        raise ValueError("Unsupported Minecraft action")
    if action in {"stop", "restart"}:
        parameters = await minecraft_parameters(project, session, settings)
        await AgentClient(settings).execute(
            "minecraft_stop",
            {key: parameters[key] for key in ("project_id", "rcon_host_port", "rcon_password")},
            request_timeout=150,
        )
    if action in {"start", "restart"}:
        await start_minecraft_runtime(project, session, settings)
