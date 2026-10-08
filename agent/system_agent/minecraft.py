"""Restricted Minecraft artifacts, graceful lifecycle and server status protocol."""

import asyncio
import contextlib
import fcntl
import hashlib
import json
import os
import re
import shutil
import socket
import stat
import struct
import time
import zipfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from http.client import HTTPSConnection
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit

from system_agent.config import settings
from system_agent.security import safe_child as resolve_child

MAX_JAR_BYTES = 512 * 1024 * 1024
MAX_STATUS_BYTES = 64 * 1024
STOP_TIMEOUT_SECONDS = 120
ID = re.compile(r"^[a-f0-9-]{36}$")
JAR = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,249}\.jar$")
HOSTS = {
    "paper": {"fill.papermc.io", "fill-data.papermc.io"},
    "purpur": {"api.purpurmc.org"},
    "spigot": {"hub.spigotmc.org"},
}
USER_AGENT = "NectarinePanel/0.1 (https://github.com/JanekDeveloper/NectarinePanel)"


@asynccontextmanager
async def mutation_lock(project_id: str) -> AsyncIterator[None]:
    """Fence helper mutations that outlive their worker's HTTP connection."""
    project_root(project_id)
    directory = safe_child(settings.storage_root, ".minecraft-agent-locks")
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(
        directory / f"{project_id}.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600
    )
    deadline = time.monotonic() + 120
    try:
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() > deadline:
                    raise ValueError("Minecraft agent operation is still active") from None
                await asyncio.sleep(0.1)
        yield
    finally:
        os.close(descriptor)


def fence_runtime(project_id: str) -> dict[str, bool]:
    """Confirm that prior helper mutations completed while holding their fence."""
    project_root(project_id)
    return {"idle": True}


def java_image(major: int, *, jdk: bool = False) -> str:
    """Select a published image including the discontinued Java 16 generation."""
    if major not in {8, 11, 16, 17, 21, 25}:
        raise ValueError("Unsupported Java version")
    if major == 16:
        return "azul/zulu-openjdk:16" if jdk else "azul/zulu-openjdk:16-jre"
    return f"eclipse-temurin:{major}-{'jdk' if jdk else 'jre'}"


def safe_child(root: Path, relative: str) -> Path:
    """Reject symlinks before resolution at every Minecraft filesystem boundary."""
    path = Path(relative)
    if path.is_absolute() or ".." in path.parts or root.is_symlink():
        raise ValueError("Unsafe Minecraft path")
    current = root
    for part in path.parts:
        current /= part
        if current.is_symlink():
            raise ValueError("Minecraft symlinks are not allowed")
    return resolve_child(root, relative)


def project_root(project_id: str) -> Path:
    """Resolve managed persistent storage for a validated project UUID."""
    if not ID.fullmatch(project_id):
        raise ValueError("Invalid project ID")
    return safe_child(settings.storage_root, f"minecraft/{project_id}")


def staging_root(project_id: str, job_id: str) -> Path:
    """Resolve job-specific staging outside live server storage."""
    project_root(project_id)
    if not ID.fullmatch(job_id):
        raise ValueError("Invalid job ID")
    return safe_child(settings.storage_root, f".minecraft-staging/{project_id}/{job_id}")


def validate_jar(path: Path, *, plugin: bool = False, engine: str = "paper") -> str:
    """Validate bounded JAR metadata without executing or extracting its code."""
    if path.is_symlink() or not path.is_file() or not 0 < path.stat().st_size <= MAX_JAR_BYTES:
        raise ValueError("Invalid JAR file")
    try:
        with zipfile.ZipFile(path) as archive:
            members = archive.infolist()
            if len(members) > 100_000 or sum(item.file_size for item in members) > 2 * 1024**3:
                raise ValueError("JAR exceeds metadata limits")
            names = archive.namelist()
            if len(set(names)) != len(names) or any(
                Path(item.filename).is_absolute()
                or ".." in Path(item.filename).parts
                or stat.S_ISLNK(item.external_attr >> 16)
                or item.flag_bits & 1
                for item in members
            ):
                raise ValueError("Unsafe JAR entries")
            if archive.testzip() is not None:
                raise ValueError("Corrupt JAR entry")
            descriptor = "plugin.yml" if plugin else "META-INF/MANIFEST.MF"
            if plugin and engine in {"paper", "purpur"} and descriptor not in names:
                descriptor = "paper-plugin.yml"
            info = archive.getinfo(descriptor)
            if info.file_size > 64 * 1024:
                raise ValueError("JAR descriptor is too large")
            metadata = archive.read(descriptor).decode("utf-8")
            if plugin:
                if not all(
                    re.search(rf"(?m)^{field}:\s*\S", metadata)
                    for field in ("name", "version", "main")
                ):
                    raise ValueError("Plugin descriptor is incomplete")
            elif not re.search(r"(?im)^Main-Class:\s*\S", metadata):
                raise ValueError("Server JAR has no main class")
    except (zipfile.BadZipFile, KeyError, UnicodeError, RuntimeError) as exc:
        raise ValueError("Invalid JAR descriptor") from exc
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def download(
    url: str,
    destination: Path,
    engine: str,
    checksum: str | None = None,
    algorithm: str = "sha256",
) -> str:
    """Stream official HTTPS artifacts with bounded redirects, size and duration."""
    if engine not in HOSTS or algorithm not in {"sha256", "md5"}:
        raise ValueError("Unsupported artifact source")
    if checksum is not None and not re.fullmatch(
        r"[A-Fa-f0-9]{64}" if algorithm == "sha256" else r"[A-Fa-f0-9]{32}", checksum
    ):
        raise ValueError("Invalid artifact checksum")
    digest = hashlib.new(algorithm, usedforsecurity=False)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(".part")
    if temporary.is_symlink() or destination.is_symlink():
        raise ValueError("Artifact cannot be a symlink")
    temporary.unlink(missing_ok=True)
    deadline = time.monotonic() + 600
    try:
        for _ in range(4):
            parsed = urlsplit(url)
            if (
                parsed.scheme != "https"
                or parsed.hostname not in HOSTS[engine]
                or parsed.port not in {None, 443}
                or parsed.username
                or parsed.password
                or parsed.fragment
            ):
                raise ValueError("Artifact host is not allowed")
            with contextlib.closing(HTTPSConnection(parsed.hostname, timeout=30)) as connection:
                connection.request(
                    "GET",
                    parsed.path + (f"?{parsed.query}" if parsed.query else ""),
                    headers={"User-Agent": USER_AGENT},
                )
                with contextlib.closing(connection.getresponse()) as response:
                    if response.status in {301, 302, 303, 307, 308}:
                        url = urljoin(url, response.getheader("Location", ""))
                        continue
                    if response.status != 200:
                        raise ValueError(f"Official artifact returned HTTP {response.status}")
                    size = 0
                    with temporary.open("xb") as output:
                        while chunk := response.read(1024 * 1024):
                            size += len(chunk)
                            if size > MAX_JAR_BYTES or time.monotonic() > deadline:
                                raise ValueError("Artifact exceeds download limits")
                            digest.update(chunk)
                            output.write(chunk)
                        output.flush()
                        os.fsync(output.fileno())
                    if size == 0 or (
                        checksum and digest.hexdigest().lower() != checksum.lower()
                    ):
                        raise ValueError("Artifact checksum mismatch")
                    temporary.replace(destination)
                    destination.chmod(0o640)
                    return validate_jar(destination)
        raise ValueError("Too many artifact redirects")
    finally:
        temporary.unlink(missing_ok=True)


async def stage_server(
    project_id: str,
    job_id: str,
    *,
    engine: str,
    artifact: dict[str, Any] | None = None,
    uploaded_jar: str | None = None,
    java_version: int = 21,
) -> dict[str, Any]:
    """Prepare a verified server JAR without touching a running server."""
    from system_agent.operations import run_command

    root = project_root(project_id)
    stage = staging_root(project_id, job_id)
    stage.mkdir(parents=True, exist_ok=True, mode=0o750)
    target = safe_child(stage, "server.jar")
    if engine not in HOSTS or java_version not in {8, 11, 16, 17, 21, 25}:
        raise ValueError("Unsupported Minecraft engine or Java")
    if (uploaded_jar is None) == (artifact is None):
        raise ValueError("Provide exactly one artifact source")
    if uploaded_jar is not None:
        if not JAR.fullmatch(uploaded_jar):
            raise ValueError("Invalid uploaded JAR name")
        source = safe_child(root, uploaded_jar)
        validate_jar(source)
        shutil.copyfile(source, target)
        result: dict[str, Any] = {"source": "upload", "java_version": java_version}
    elif artifact is not None:
        if artifact.get("engine") != engine:
            raise ValueError("Artifact engine mismatch")
        result = {
            key: value
            for key, value in artifact.items()
            if key not in {"url", "checksum", "checksum_algorithm"}
        }
        result["source"] = "official"
        if engine != "spigot":
            await asyncio.to_thread(
                download,
                artifact["url"],
                target,
                engine,
                artifact["checksum"],
                artifact["checksum_algorithm"],
            )
        else:
            revision = str(artifact["minecraft_version"])
            tools_build = int(artifact["buildtools_build"])
            if not re.fullmatch(r"\d+(?:\.\d+){1,3}", revision) or tools_build < 1:
                raise ValueError("Invalid Spigot build coordinates")
            tools = stage / "BuildTools.jar"
            await asyncio.to_thread(
                download,
                f"https://hub.spigotmc.org/jenkins/job/BuildTools/{tools_build}/artifact/target/BuildTools.jar",
                tools,
                "spigot",
            )
            result["buildtools_sha256"] = validate_jar(tools)
            owner = root.stat()
            if owner.st_uid == 0:
                raise ValueError("Minecraft build must not run as root")
            os.chown(stage, owner.st_uid, owner.st_gid)
            os.chown(tools, owner.st_uid, owner.st_gid)
            image = f"nectarine-spigot-builder:{java_version}"
            context = Path(__file__).parent / "assets"
            await run_command(
                [
                    str(settings.docker_binary),
                    "build",
                    "--tag",
                    image,
                    "--build-arg",
                    f"JAVA_IMAGE={java_image(java_version, jdk=True)}",
                    "--file",
                    str(context / "spigot.Dockerfile"),
                    str(context),
                ],
                command_timeout=600,
            )
            container = f"np-mc-build-{job_id}"
            try:
                await run_command(
                    [
                        str(settings.docker_binary),
                        "run",
                        "--name",
                        container,
                        "--rm",
                        "--cap-drop",
                        "ALL",
                        "--security-opt",
                        "no-new-privileges:true",
                        "--pids-limit",
                        "512",
                        "--memory",
                        "4g",
                        "--cpus",
                        "2",
                        "--user",
                        f"{owner.st_uid}:{owner.st_gid}",
                        "--volume",
                        f"{stage}:/build",
                        image,
                        "java",
                        "-Xmx2G",
                        "-jar",
                        "BuildTools.jar",
                        "--rev",
                        revision,
                        "--compile",
                        "SPIGOT",
                    ],
                    command_timeout=1800,
                )
                references = {}
                for repository in ("BuildData", "Bukkit", "CraftBukkit", "Spigot"):
                    repository_root = safe_child(stage, repository)
                    head = (repository_root / ".git" / "HEAD").read_text().strip()
                    if head.startswith("ref: "):
                        reference = safe_child(repository_root / ".git", head[5:])
                        head = reference.read_text().strip()
                    if not re.fullmatch(r"[0-9a-f]{40}", head):
                        raise ValueError("BuildTools source revision is invalid")
                    expected = artifact.get("refs", {}).get(repository)
                    if expected and head != expected:
                        raise ValueError(
                            "BuildTools source revision differs from selected build"
                        )
                    references[repository] = head
                result["refs"] = references
                candidates = list(stage.glob("spigot-*.jar"))
                if len(candidates) != 1:
                    raise ValueError("BuildTools did not produce one server JAR")
                validate_jar(candidates[0])
                candidates[0].replace(target)
            finally:
                with contextlib.suppress(Exception):
                    await run_command([str(settings.docker_binary), "rm", "--force", container])
    else:
        raise ValueError("Artifact source missing")
    return {
        **result,
        "sha256": validate_jar(target),
        "launcher": "server.jar",
        "launch_mode": "jar",
    }


def publish_server(project_id: str, job_id: str, sha256: str) -> dict[str, Any]:
    """Atomically publish an unchanged job-owned staged JAR."""
    root = project_root(project_id)
    source = safe_child(staging_root(project_id, job_id), "server.jar")
    if validate_jar(source) != sha256:
        raise ValueError("Staged artifact changed")
    destination = safe_child(root, "server.jar")
    owner = root.stat()
    os.chown(source, owner.st_uid, owner.st_gid)
    source.chmod(0o640)
    source.replace(destination)
    return {"launcher": "server.jar", "sha256": sha256}


async def cleanup_stage(project_id: str, job_id: str) -> dict[str, bool]:
    """Remove only one operation's temporary build container and directory."""
    from system_agent.operations import run_command

    stage = staging_root(project_id, job_id)
    with contextlib.suppress(Exception):
        await run_command(
            [str(settings.docker_binary), "rm", "--force", f"np-mc-build-{job_id}"]
        )
    await asyncio.to_thread(shutil.rmtree, stage, True)
    return {"cleaned": True}


async def stop_server(
    project_id: str, *, rcon_host_port: int = 25575, rcon_password: str | None = None
) -> dict[str, bool]:
    """Stop Minecraft and wait for shutdown without automatic SIGKILL."""
    from system_agent.operations import _minecraft_container_running, _rcon_request, run_command

    project_root(project_id)
    if not 1024 <= rcon_host_port <= 65535:
        raise ValueError("Invalid RCON port")
    if not await _minecraft_container_running(project_id):
        return {"stopped": True}
    await run_command(
        [str(settings.docker_binary), "update", "--restart=no", f"vps-project-{project_id}"]
    )
    if rcon_password:
        with contextlib.suppress(OSError, RuntimeError):
            await asyncio.to_thread(_rcon_request, rcon_host_port, rcon_password, "stop")
    else:
        await run_command(
            [
                str(settings.docker_binary),
                "kill",
                "--signal=SIGTERM",
                f"vps-project-{project_id}",
            ]
        )
    deadline = time.monotonic() + STOP_TIMEOUT_SECONDS
    while await _minecraft_container_running(project_id):
        if time.monotonic() > deadline:
            raise ValueError("Minecraft did not stop; live files were preserved")
        await asyncio.sleep(1)
    return {"stopped": True}


def _varint(value: int) -> bytes:
    """Encode a Minecraft signed 32-bit VarInt."""
    value &= 0xFFFFFFFF
    result = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        result.append(byte | (0x80 if value else 0))
        if not value:
            return bytes(result)


def _read_exact(
    connection: socket.socket, size: int, *, deadline: float | None = None
) -> bytes:
    """Read one bounded protocol segment despite TCP fragmentation."""
    if not 0 <= size <= MAX_STATUS_BYTES:
        raise ValueError("Status packet too large")
    data = bytearray()
    while len(data) < size:
        if deadline is not None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Minecraft status deadline exceeded")
            connection.settimeout(remaining)
        chunk = connection.recv(size - len(data))
        if not chunk:
            raise ValueError("Incomplete status packet")
        data.extend(chunk)
    return bytes(data)


def _read_varint(connection: socket.socket, *, deadline: float | None = None) -> int:
    """Decode a bounded unsigned Minecraft VarInt."""
    result = 0
    for index in range(5):
        byte = _read_exact(connection, 1, deadline=deadline)[0]
        result |= (byte & 0x7F) << (index * 7)
        if not byte & 0x80:
            if result > MAX_STATUS_BYTES:
                raise ValueError("Status length exceeds limit")
            return result
    raise ValueError("Malformed status VarInt")


def _ping(port: int) -> dict[str, Any]:
    """Read a bounded Server List Ping response from local managed storage."""
    host = b"127.0.0.1"
    handshake = (
        b"\x00" + _varint(-1) + _varint(len(host)) + host + struct.pack(">H", port) + b"\x01"
    )
    deadline = time.monotonic() + 3
    with socket.create_connection(("127.0.0.1", port), timeout=3) as connection:
        connection.sendall(_varint(len(handshake)) + handshake + b"\x01\x00")
        length = _read_varint(connection, deadline=deadline)
        if _read_exact(connection, 1, deadline=deadline) != b"\x00":
            raise ValueError("Invalid status packet ID")
        json_length = _read_varint(connection, deadline=deadline)
        if json_length + 1 + len(_varint(json_length)) != length:
            raise ValueError("Invalid status frame length")
        payload = json.loads(_read_exact(connection, json_length, deadline=deadline))
    if not isinstance(payload, dict) or not isinstance(payload.get("players", {}), dict):
        raise ValueError("Invalid status document")
    players = payload.get("players", {})
    online, maximum = int(players.get("online", 0)), int(players.get("max", 0))
    if not 0 <= online <= 1_000_000 or not 0 <= maximum <= 1_000_000:
        raise ValueError("Invalid player counts")
    return {
        "available": True,
        "online_players": online,
        "max_players": maximum,
        "motd": payload.get("description", ""),
        "version": str(payload.get("version", {}).get("name", ""))[:128],
        "sample": players.get("sample", [])[:100],
    }


async def server_status(project_id: str, game_port: int) -> dict[str, Any]:
    """Probe only the project container's verified loopback game port."""
    from system_agent.operations import _minecraft_container_running, run_command

    project_root(project_id)
    running = await _minecraft_container_running(project_id)
    if not 1024 <= game_port <= 65535:
        raise ValueError("Invalid game port")
    try:
        inspected = await run_command(
            [
                str(settings.docker_binary),
                "inspect",
                "--format",
                "{{json .NetworkSettings.Ports}}",
                f"vps-project-{project_id}",
            ]
        )
        ports = json.loads(inspected["stdout"])
        bindings = ports.get(f"{game_port}/tcp") or []
        if not any(int(item["HostPort"]) == game_port for item in bindings):
            raise ValueError("Game port does not belong to project")
        return {
            "running": running,
            **await asyncio.wait_for(asyncio.to_thread(_ping, game_port), timeout=5),
        }
    except (OSError, ValueError, TypeError, AttributeError, RuntimeError, TimeoutError):
        return {
            "running": running,
            "available": False,
            "online_players": None,
            "max_players": None,
            "motd": None,
            "version": None,
            "sample": [],
        }


async def plugin_operation(
    project_id: str,
    *,
    action: str,
    name: str = "",
    staged_path: str | None = None,
    engine: str = "paper",
) -> dict[str, Any]:
    """List or mutate validated plugin files without loading their code."""
    from system_agent.operations import _minecraft_container_running

    root = project_root(project_id)
    directory = safe_child(root, "plugins")
    if action == "list":
        if not directory.exists():
            return {"plugins": []}
        entries = []
        for item in sorted(directory.iterdir()):
            enabled = item.name.endswith(".jar")
            jar_name = item.name if enabled else item.name.removesuffix(".disabled")
            if JAR.fullmatch(jar_name) and item.is_file() and not item.is_symlink():
                entries.append(
                    {
                        "name": jar_name,
                        "path": f"plugins/{item.name}",
                        "size_bytes": item.stat().st_size,
                        "enabled": enabled,
                    }
                )
        return {"plugins": entries}
    if await _minecraft_container_running(project_id):
        raise ValueError("Stop Minecraft before changing plugins")
    directory.mkdir(exist_ok=True, mode=0o750)
    owner = root.stat()
    os.chown(directory, owner.st_uid, owner.st_gid)
    if not JAR.fullmatch(name):
        raise ValueError("Invalid plugin name")
    enabled_path = safe_child(directory, name)
    disabled_path = safe_child(directory, f"{name}.disabled")
    if action == "upload":
        staging = settings.storage_root / ".uploads"
        source = safe_child(staging, Path(staged_path or "").name)
        if str(source) != staged_path:
            raise ValueError("Invalid plugin staging path")
        validate_jar(source, plugin=True, engine=engine)
        temporary = safe_child(directory, f".{name}.upload")
        try:
            shutil.copyfile(source, temporary)
            owner = root.stat()
            os.chown(temporary, owner.st_uid, owner.st_gid)
            temporary.chmod(0o640)
            temporary.replace(disabled_path if disabled_path.exists() else enabled_path)
        finally:
            temporary.unlink(missing_ok=True)
    elif action in {"enable", "disable"}:
        source, target = (
            (disabled_path, enabled_path)
            if action == "enable"
            else (enabled_path, disabled_path)
        )
        if not source.is_file() or target.exists():
            raise ValueError("Plugin state conflict")
        source.replace(target)
    elif action == "delete":
        enabled_path.unlink(missing_ok=True)
        disabled_path.unlink(missing_ok=True)
    else:
        raise ValueError("Unsupported plugin action")
    return {"name": name, "action": action}
