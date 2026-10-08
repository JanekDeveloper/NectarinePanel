"""Bounded official release catalogs for Bukkit-compatible Minecraft servers."""

import asyncio
import re
import time
from typing import Any

import httpx

from app.services.minecraft import MinecraftCatalogError

USER_AGENT = "NectarinePanel/0.1 (https://github.com/JanekDeveloper/NectarinePanel)"
VERSION = re.compile(r"^\d+(?:\.\d+){1,3}$")
PAPER = "https://fill.papermc.io/v3/projects/paper"
PURPUR = "https://api.purpurmc.org/v2/purpur"
SPIGOT = "https://hub.spigotmc.org/versions"
MAX_BYTES = 4 * 1024 * 1024
_cache: dict[str, tuple[float, Any]] = {}


def java_for_engine(engine: str, version: str) -> int:
    """Select the documented Java generation without modifying Forge policy."""
    if not VERSION.fullmatch(version):
        raise ValueError("Invalid Minecraft release")
    key = tuple(int(part) for part in version.split("."))
    if key >= (26, 1):
        return 25
    if engine in {"paper", "purpur"}:
        if key >= (1, 20):
            return 21
        if key >= (1, 17):
            return 17
        if key >= (1, 16, 5):
            return 16
        if key >= (1, 12):
            return 11
        return 8
    if key >= (1, 20, 5):
        return 21
    if key >= (1, 18):
        return 17
    return 16 if key >= (1, 17) else 8


async def official_response(url: str, *, text: bool = False) -> Any:
    """Fetch a fixed official endpoint with streaming size and time limits."""
    cached = _cache.get(url)
    if cached and time.monotonic() - cached[0] < 3600:
        return cached[1]
    try:
        async with httpx.AsyncClient(
            timeout=15, follow_redirects=False, headers={"User-Agent": USER_AGENT}
        ) as client:
            async with client.stream("GET", url) as response:
                response.raise_for_status()
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > MAX_BYTES:
                        raise MinecraftCatalogError("Official catalog exceeds size limit")
                import json

                value = body.decode("utf-8") if text else json.loads(body)
    except (httpx.HTTPError, ValueError, UnicodeError) as exc:
        raise MinecraftCatalogError("Official Minecraft catalog unavailable") from exc
    _cache[url] = (time.monotonic(), value)
    return value


async def server_versions(engine: str) -> list[dict[str, Any]]:
    """List release versions without eagerly loading every build catalog."""
    if engine == "paper":
        payload = await official_response(PAPER)
        versions = [version for group in payload["versions"].values() for version in group]
    elif engine == "purpur":
        versions = (await official_response(PURPUR))["versions"]
    elif engine == "spigot":
        page = await official_response(f"{SPIGOT}/", text=True)
        versions = re.findall(r'href="(\d+(?:\.\d+){1,3})\.json"', page)
    else:
        raise ValueError("Unsupported Minecraft engine")
    return [
        {
            "minecraft_version": version,
            "forge_versions": [],
            "java_version": java_for_engine(engine, version),
        }
        for version in sorted(
            {v for v in versions if isinstance(v, str) and VERSION.fullmatch(v)},
            key=lambda v: tuple(map(int, v.split("."))),
            reverse=True,
        )
    ]


async def server_builds(engine: str, version: str) -> list[dict[str, Any]]:
    """Return selectable successful release builds with source-specific channels."""
    if not VERSION.fullmatch(version):
        raise ValueError("Invalid Minecraft release")
    if engine == "paper":
        payload = await official_response(f"{PAPER}/versions/{version}/builds")
        return [
            {
                "build_id": str(build["id"]),
                "channel": "stable",
                "java_version": java_for_engine(engine, version),
            }
            for build in payload
            if build.get("channel") == "STABLE"
            and "server:default" in build.get("downloads", {})
        ]
    if engine == "purpur":
        payload = await official_response(f"{PURPUR}/{version}")
        semaphore = asyncio.Semaphore(8)

        async def successful(build_id: object) -> bool:
            """Check build outcomes with bounded upstream concurrency and shared caching."""
            if not re.fullmatch(r"[0-9]{1,12}", str(build_id)):
                return False
            async with semaphore:
                metadata = await official_response(f"{PURPUR}/{version}/{build_id}")
                return bool(metadata.get("result") == "SUCCESS")

        ids = list(reversed(payload["builds"]["all"]))
        outcomes = await asyncio.gather(*(successful(build) for build in ids))
        return [
            {
                "build_id": str(build),
                "channel": "unknown",
                "java_version": java_for_engine(engine, version),
            }
            for build, success in zip(ids, outcomes, strict=True)
            if success
        ]
    if engine == "spigot":
        payload = await official_response(f"{SPIGOT}/{version}.json")
        java = java_for_engine(engine, version)
        majors = payload.get("javaVersions", [])
        if majors:
            java = max(java, int(majors[0]) - 44)
        return [{"build_id": str(payload["name"]), "channel": "release", "java_version": java}]
    raise ValueError("Unsupported Minecraft engine")


async def resolve_artifact(engine: str, version: str, build_id: str) -> dict[str, Any]:
    """Resolve a selected build exclusively through official metadata."""
    builds = await server_builds(engine, version)
    build = next((item for item in builds if item["build_id"] == build_id), None)
    if build is None:
        raise ValueError("Unsupported Minecraft build")
    result: dict[str, Any] = {"engine": engine, "minecraft_version": version, **build}
    if engine == "paper":
        payload = await official_response(f"{PAPER}/versions/{version}/builds")
        download = next(item for item in payload if str(item["id"]) == build_id)["downloads"][
            "server:default"
        ]
        result.update(
            url=download["url"],
            checksum=download["checksums"]["sha256"],
            checksum_algorithm="sha256",
        )
    elif engine == "purpur":
        payload = await official_response(f"{PURPUR}/{version}/{build_id}")
        if payload.get("result") != "SUCCESS":
            raise ValueError("Purpur build is not successful")
        result.update(
            url=f"{PURPUR}/{version}/{build_id}/download",
            checksum=payload["md5"],
            checksum_algorithm="md5",
        )
    else:
        payload = await official_response(f"{SPIGOT}/{version}.json")
        tools = await official_response(
            "https://hub.spigotmc.org/jenkins/job/BuildTools/lastSuccessfulBuild/api/json"
        )
        result.update(refs=payload["refs"], buildtools_build=int(tools["number"]))
    return result
