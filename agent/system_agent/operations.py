"""Allowlisted privileged system operations."""

import asyncio
import contextlib
import hashlib
import json
import os
import pwd
import re
import shlex
import shutil
import socket
import stat
import struct
import tarfile
import tempfile
import uuid
import zipfile
from datetime import UTC, datetime
from http.client import HTTPResponse, HTTPSConnection
from pathlib import Path
from typing import Any

import psutil

from system_agent.config import settings
from system_agent.security import safe_child

HOSTNAME_PATTERN = re.compile(
    r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$"
)
EMAIL_PATTERN = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,190}$")
SERVICE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.@-]{0,127}\.service$")
CONTAINER_PATTERN = re.compile(r"^vps-project-[a-f0-9-]{36}$")
PROJECT_ID_PATTERN = re.compile(r"^[a-f0-9-]{36}$")
DATABASE_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9_$][A-Za-z0-9_$.-]{0,62}$")
RELEASE_ID_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")
DOWNLOAD_ID_PATTERN = re.compile(r"^[a-f0-9]{32}$")
MAX_ARCHIVE_MEMBERS = 100_000
MAX_ARCHIVE_EXPANDED_BYTES = 20 * 1024 * 1024 * 1024
MOUNT_UNIT_PATTERN = re.compile(r"^(?:[A-Za-z0-9_.@-]|\\x[0-9A-Fa-f]{2})+\.mount$")
SERVICE_ENV_KEY_PATTERN = re.compile(r"^[A-Z_][A-Z0-9_]{0,254}$")
DOCKER_RESOURCE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,255}$")
DOCKER_MOUNT_SOURCE_PATTERN = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9_.-]*(?:/[A-Za-z0-9][A-Za-z0-9_.-]*){0,7}$"
)
DOCKER_MOUNT_TARGET_PATTERN = re.compile(
    r"^/[A-Za-z0-9][A-Za-z0-9_.-]*(?:/[A-Za-z0-9][A-Za-z0-9_.-]*){0,15}$"
)
COMPOSE_SERVICE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
LINUX_NAME_PATTERN = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")
RUNTIME_TYPES = frozenset(
    {"docker", "docker_compose", "systemd", "pm2", "static", "minecraft_forge"}
)
EXTERNAL_PROJECT_ROOTS = (Path("/home"), Path("/opt"), Path("/srv"), Path("/var/www"))
FORGE_MAVEN_HOST = "maven.minecraftforge.net"
FORGE_VERSION_PATTERN = re.compile(r"^\d+(?:\.\d+){1,3}$")
FORGE_INSTALLER_MAX_BYTES = 256 * 1024 * 1024


class CommandError(RuntimeError):
    """Raised when an allowlisted command fails."""


async def run_command(
    arguments: list[str],
    command_timeout: int = 60,
    *,
    stdin_data: bytes | None = None,
    output_limit: int = 32768,
    cwd: Path | None = None,
    env_overrides: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Run a fixed argv command without a shell and return bounded output."""
    if not 1024 <= output_limit <= 1024 * 1024:
        raise ValueError("Command output limit is invalid")
    environment = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C.UTF-8"}
    if env_overrides:
        for key, value in env_overrides.items():
            if not SERVICE_ENV_KEY_PATTERN.fullmatch(key) or "\x00" in value:
                raise ValueError("Invalid command environment")
        environment.update(env_overrides)
    process = await asyncio.create_subprocess_exec(
        *arguments,
        stdin=asyncio.subprocess.PIPE if stdin_data is not None else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(cwd) if cwd else None,
        env=environment,
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            process.communicate(stdin_data),
            timeout=command_timeout,
        )
    except TimeoutError as exc:
        process.kill()
        await process.wait()
        raise CommandError("Command timed out") from exc
    output = stdout.decode("utf-8", errors="replace")[-output_limit:]
    error = stderr.decode("utf-8", errors="replace")[-output_limit:]
    if process.returncode != 0:
        raise CommandError(error or f"Command failed with exit code {process.returncode}")
    return {"exit_code": process.returncode, "stdout": output, "stderr": error}


def create_directory(relative_path: str, mode: int = 0o750) -> dict[str, Any]:
    """Create a directory under the panel storage root."""
    match = re.fullmatch(r"(projects|minecraft|sftp)/([a-f0-9-]{36})", relative_path)
    if match is None or not PROJECT_ID_PATTERN.fullmatch(match.group(2)):
        raise ValueError("Only project directories may be created")
    if mode not in {0o700, 0o750, 0o770}:
        raise ValueError("Unsupported project directory mode")
    path = settings.storage_root.resolve() / match.group(1) / match.group(2)
    if path.is_symlink():
        raise ValueError("Project directory cannot be a symlink")
    path.mkdir(parents=True, exist_ok=True, mode=mode)
    os.chmod(path, mode)
    return {"path": str(path), "mode": oct(mode)}


def remove_directory(relative_path: str) -> dict[str, Any]:
    """Remove an allowed project directory without following symlinks."""
    match = re.fullmatch(r"(projects|minecraft)/([a-f0-9-]{36})", relative_path)
    if match is None or not PROJECT_ID_PATTERN.fullmatch(match.group(2)):
        raise ValueError("Only project directories may be removed")
    path = settings.storage_root.resolve() / match.group(1) / match.group(2)
    if path.is_symlink():
        path.unlink()
    elif path.exists():
        shutil.rmtree(path)
    return {"removed": str(path)}


def _external_project_root(root: str) -> Path:
    """Validate an absolute imported project root."""
    candidate = Path(root)
    if not candidate.is_absolute():
        raise ValueError("Project root must be absolute")
    resolved = candidate.resolve()
    if not any(
        resolved == allowed_root or allowed_root in resolved.parents
        for allowed_root in EXTERNAL_PROJECT_ROOTS
    ):
        raise ValueError("Project root is outside allowed roots")
    return resolved


def _resolve_external_child(root: Path, relative_path: str) -> Path:
    """Resolve a path under an imported project root."""
    candidate = (root / relative_path.lstrip("/")).resolve()
    if candidate != root and root not in candidate.parents:
        raise ValueError("Path escapes the allowed root")
    return candidate


def _resolve_external_entry(root: Path, relative_path: str) -> Path:
    """Resolve an entry parent without following the final symbolic link."""
    relative = Path(relative_path)
    if (
        not relative_path
        or relative.is_absolute()
        or relative.name in {"", ".", ".."}
        or any(part == ".." for part in relative.parts)
        or any(ord(character) < 32 for character in relative_path)
    ):
        raise ValueError("Invalid project path")
    parent = (root / relative.parent).resolve()
    if parent != root and root not in parent.parents:
        raise ValueError("Path escapes the allowed root")
    return parent / relative.name


def list_directory(
    root: str,
    path: str = "",
    *,
    include_hidden: bool = False,
) -> dict[str, Any]:
    """List one imported project directory without following symlink targets."""
    project_root = _external_project_root(root)
    directory = _resolve_external_child(project_root, path)
    if not directory.is_dir():
        raise ValueError("Directory not found")
    entries = []
    for item in directory.iterdir():
        if not include_hidden and item.name.startswith("."):
            continue
        metadata = item.lstat()
        target_kind: str | None = None
        target: Path | None = None
        if item.is_symlink():
            kind = "symlink"
            try:
                resolved_target = item.resolve(strict=True)
                if resolved_target == project_root or project_root in resolved_target.parents:
                    target = resolved_target
                    if resolved_target.is_dir():
                        target_kind = "directory"
                    elif resolved_target.is_file():
                        target_kind = "file"
            except OSError:
                pass
        elif item.is_dir():
            kind = "directory"
            target = item
        else:
            kind = "file"
            target = item
        entries.append(
            {
                "name": item.name,
                "path": item.relative_to(project_root).as_posix(),
                "kind": kind,
                "target_kind": target_kind,
                "size_bytes": (
                    target.stat().st_size
                    if target is not None and (kind == "file" or target_kind == "file")
                    else _bounded_directory_size(target)
                    if target is not None
                    and (kind == "directory" or target_kind == "directory")
                    else 0
                ),
                "modified_at": datetime.fromtimestamp(
                    metadata.st_mtime,
                    tz=UTC,
                ).isoformat(),
                "permissions": stat.filemode(metadata.st_mode),
            }
        )
    return {
        "entries": sorted(
            entries,
            key=lambda item: (
                item["kind"] != "directory" and item["target_kind"] != "directory",
                str(item["name"]).lower(),
            ),
        )
    }


def _staging_root() -> Path:
    """Return a private download staging directory owned by the panel user."""
    root = settings.storage_root.resolve() / ".downloads"
    root.mkdir(parents=True, exist_ok=True, mode=0o750)
    account = pwd.getpwnam(settings.project_runtime_user)
    os.chown(root, account.pw_uid, account.pw_gid)
    os.chmod(root, 0o750)  # noqa: S103 - private panel group requires traversal
    return root


def stage_file_download(root: str, path: str, download_id: str) -> dict[str, Any]:
    """Copy one protected imported-project file into private panel storage."""
    if not DOWNLOAD_ID_PATTERN.fullmatch(download_id):
        raise ValueError("Invalid download identifier")
    project_root = _external_project_root(root)
    source = _resolve_external_entry(project_root, path)
    try:
        metadata = source.lstat()
    except FileNotFoundError as exc:
        raise ValueError("File not found") from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise ValueError("File not found")

    staging_root = _staging_root()
    destination = staging_root / download_id
    temporary = staging_root / f".{download_id}.tmp"
    source_fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        if not stat.S_ISREG(os.fstat(source_fd).st_mode):
            raise ValueError("File not found")
        destination_fd = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
        )
        try:
            with os.fdopen(source_fd, "rb", closefd=False) as source_file:
                with os.fdopen(destination_fd, "wb", closefd=False) as destination_file:
                    shutil.copyfileobj(source_file, destination_file, length=1024 * 1024)
        finally:
            os.close(destination_fd)
        account = pwd.getpwnam(settings.project_runtime_user)
        os.chown(temporary, account.pw_uid, account.pw_gid)
        os.chmod(temporary, 0o640)
        temporary.replace(destination)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    finally:
        os.close(source_fd)
    return {
        "path": str(destination),
        "size_bytes": destination.stat().st_size,
    }


def publish_staged_upload(root: str, path: str, staged_path: str) -> dict[str, Any]:
    """Copy a panel-owned staged upload into a protected project root."""
    project_root = _external_project_root(root)
    destination = _resolve_external_entry(project_root, path)
    staging_root = (settings.storage_root.resolve() / ".uploads").resolve()
    source = Path(staged_path).resolve(strict=True)
    if source.parent != staging_root or not source.is_file() or source.is_symlink():
        raise ValueError("Invalid staged upload")
    try:
        destination.lstat()
    except FileNotFoundError:
        pass
    else:
        raise ValueError("Destination already exists")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.upload")
    try:
        with source.open("rb") as source_file, temporary.open("xb") as destination_file:
            shutil.copyfileobj(source_file, destination_file, length=1024 * 1024)
        os.chmod(temporary, 0o640)
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    return {
        "size_bytes": destination.stat().st_size,
        "modified_at": datetime.fromtimestamp(destination.stat().st_mtime, tz=UTC).isoformat(),
    }


def _archive_filter(member: tarfile.TarInfo) -> tarfile.TarInfo | None:
    """Exclude links and special files from imported-project archives."""
    if not (member.isfile() or member.isdir()):
        return None
    member.mode &= 0o777
    member.uid = 0
    member.gid = 0
    member.uname = ""
    member.gname = ""
    return member


def archive_path(root: str, source: str, destination: str) -> dict[str, Any]:
    """Create an archive inside a protected imported project root."""
    project_root = _external_project_root(root)
    source_path = _resolve_external_entry(project_root, source)
    destination_path = _resolve_external_entry(project_root, destination)
    try:
        source_metadata = source_path.lstat()
    except FileNotFoundError as exc:
        raise ValueError("Archive source not found") from exc
    if stat.S_ISLNK(source_metadata.st_mode) or not (
        stat.S_ISREG(source_metadata.st_mode) or stat.S_ISDIR(source_metadata.st_mode)
    ):
        raise ValueError("Archive source not found")
    try:
        destination_path.lstat()
    except FileNotFoundError:
        pass
    else:
        raise ValueError("Archive destination already exists")
    if stat.S_ISDIR(source_metadata.st_mode) and (
        destination_path == source_path or source_path in destination_path.parents
    ):
        raise ValueError("Archive destination cannot be inside its source")
    destination_name = destination_path.name.lower()
    if not (destination_name.endswith(".zip") or destination_name.endswith(".tar.gz")):
        raise ValueError("Archive destination must be ZIP or TAR.GZ")

    destination_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        if destination_name.endswith(".tar.gz"):
            with tarfile.open(destination_path, "x:gz", format=tarfile.PAX_FORMAT) as archive:
                archive.add(
                    source_path,
                    arcname=source_path.relative_to(project_root),
                    recursive=True,
                    filter=_archive_filter,
                )
        else:
            with zipfile.ZipFile(
                destination_path,
                "x",
                compression=zipfile.ZIP_DEFLATED,
                compresslevel=6,
            ) as archive:
                paths = [source_path]
                if stat.S_ISDIR(source_metadata.st_mode):
                    paths.extend(source_path.rglob("*"))
                for item in paths:
                    item_metadata = item.lstat()
                    if stat.S_ISLNK(item_metadata.st_mode) or not (
                        stat.S_ISREG(item_metadata.st_mode)
                        or stat.S_ISDIR(item_metadata.st_mode)
                    ):
                        continue
                    archive.write(item, item.relative_to(project_root))
        os.chown(destination_path, project_root.stat().st_uid, project_root.stat().st_gid)
        os.chmod(destination_path, 0o640)
    except Exception:
        destination_path.unlink(missing_ok=True)
        raise
    return {
        "path": destination,
        "size_bytes": destination_path.stat().st_size,
    }


def _archive_target(destination: Path, member_name: str) -> Path:
    """Resolve one archive member and reject traversal or file collisions."""
    target = (destination / member_name).resolve()
    if destination != target and destination not in target.parents:
        raise ValueError(f"Unsafe archive path: {member_name}")
    if target.exists() and not target.is_dir():
        raise ValueError(f"Archive entry already exists: {member_name}")
    return target


def extract_archive(root: str, source: str, destination: str) -> dict[str, Any]:
    """Extract a ZIP or TAR.GZ archive into an imported project directory safely."""
    project_root = _external_project_root(root)
    source_path = _resolve_external_entry(project_root, source)
    destination_path = _resolve_external_child(project_root, destination)
    try:
        source_metadata = source_path.lstat()
    except FileNotFoundError as exc:
        raise ValueError("Archive source not found") from exc
    source_name = source_path.name.lower()
    if stat.S_ISLNK(source_metadata.st_mode) or not stat.S_ISREG(source_metadata.st_mode):
        raise ValueError("Archive source not found")
    if not (source_name.endswith(".zip") or source_name.endswith(".tar.gz")):
        raise ValueError("Source must be a ZIP or TAR.GZ file")
    if destination_path.exists() and not destination_path.is_dir():
        raise ValueError("Extraction destination must be a directory")
    destination_path.mkdir(parents=True, exist_ok=True)
    if source_name.endswith(".zip"):
        with zipfile.ZipFile(source_path) as archive:
            members = archive.infolist()
            if len(members) > MAX_ARCHIVE_MEMBERS:
                raise ValueError("Archive contains too many entries")
            if sum(member.file_size for member in members) > MAX_ARCHIVE_EXPANDED_BYTES:
                raise ValueError("Archive expanded size exceeds the safety limit")
            targets: list[tuple[zipfile.ZipInfo, Path]] = []
            for member in members:
                mode = member.external_attr >> 16
                if mode and stat.S_ISLNK(mode):
                    raise ValueError("Archive symlinks are not allowed")
                targets.append((member, _archive_target(destination_path, member.filename)))
            for member, target in targets:
                if member.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(member) as source_file, target.open("xb") as target_file:
                    shutil.copyfileobj(source_file, target_file, length=1024 * 1024)
    else:
        with tarfile.open(source_path, "r:gz") as archive:
            tar_members = archive.getmembers()
            if len(tar_members) > MAX_ARCHIVE_MEMBERS:
                raise ValueError("Archive contains too many entries")
            if sum(member.size for member in tar_members) > MAX_ARCHIVE_EXPANDED_BYTES:
                raise ValueError("Archive expanded size exceeds the safety limit")
            tar_targets: list[tuple[tarfile.TarInfo, Path]] = []
            for tar_member in tar_members:
                if (
                    not (tar_member.isfile() or tar_member.isdir())
                    or tar_member.issym()
                    or tar_member.islnk()
                ):
                    raise ValueError("Archive links and special files are not allowed")
                tar_targets.append(
                    (tar_member, _archive_target(destination_path, tar_member.name))
                )
            for tar_member, target in tar_targets:
                if tar_member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                tar_source_file = archive.extractfile(tar_member)
                if tar_source_file is None:
                    raise ValueError("Archive member is unavailable")
                with tar_source_file, target.open("xb") as target_file:
                    shutil.copyfileobj(tar_source_file, target_file, length=1024 * 1024)
    return {"path": destination}


def delete_path(root: str, path: str) -> dict[str, Any]:
    """Delete one imported project entry without escaping or following symlinks."""
    project_root = _external_project_root(root)
    target = _resolve_external_entry(project_root, path)
    try:
        metadata = target.lstat()
    except FileNotFoundError as exc:
        raise ValueError("Path not found") from exc
    if stat.S_ISDIR(metadata.st_mode) and not stat.S_ISLNK(metadata.st_mode):
        shutil.rmtree(target)
    else:
        target.unlink()
    return {"removed": path}


def move_path(root: str, source: str, destination: str) -> dict[str, Any]:
    """Move one imported project entry between validated in-root paths."""
    project_root = _external_project_root(root)
    source_path = _resolve_external_entry(project_root, source)
    destination_path = _resolve_external_entry(project_root, destination)
    if source_path == destination_path:
        raise ValueError("Source and destination are identical")
    try:
        source_metadata = source_path.lstat()
    except FileNotFoundError as exc:
        raise ValueError("Source path not found") from exc
    try:
        destination_path.lstat()
    except FileNotFoundError:
        pass
    else:
        raise ValueError("Destination already exists")
    if stat.S_ISDIR(source_metadata.st_mode) and source_path in destination_path.parents:
        raise ValueError("Directory cannot be moved inside itself")
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.replace(destination_path)
    metadata = destination_path.lstat()
    kind = (
        "symlink"
        if stat.S_ISLNK(metadata.st_mode)
        else "directory"
        if stat.S_ISDIR(metadata.st_mode)
        else "file"
    )
    return {
        "entry": {
            "name": destination_path.name,
            "path": destination_path.relative_to(project_root).as_posix(),
            "kind": kind,
            "size_bytes": (
                metadata.st_size
                if kind == "file"
                else _bounded_directory_size(destination_path)
                if kind == "directory"
                else 0
            ),
            "modified_at": datetime.fromtimestamp(
                metadata.st_mtime,
                tz=UTC,
            ).isoformat(),
            "permissions": stat.filemode(metadata.st_mode),
        }
    }


def _validate_hostname(hostname: str) -> None:
    """Validate a normalized DNS hostname."""
    if not HOSTNAME_PATTERN.fullmatch(hostname):
        raise ValueError("Invalid hostname")


def _write_nginx_config(hostname: str, content: str) -> Path:
    """Atomically persist an internally generated Nginx configuration."""
    settings.nginx_config_root.mkdir(parents=True, exist_ok=True)
    target = safe_child(settings.nginx_config_root, f"{hostname}.conf")
    temporary = target.with_suffix(".conf.tmp")
    temporary.write_text(content, encoding="utf-8")
    os.chmod(temporary, 0o640)
    temporary.replace(target)
    return target


def configure_proxy(
    hostname: str,
    upstream_port: int,
    *,
    ssl_enabled: bool = False,
    max_upload_mb: int = 512,
) -> dict[str, Any]:
    """Generate an Nginx reverse proxy from strictly bounded fields."""
    _validate_hostname(hostname)
    if not 1024 <= upstream_port <= 65535:
        raise ValueError("Upstream port must be between 1024 and 65535")
    if not 1 <= max_upload_mb <= 4096:
        raise ValueError("Upload limit must be between 1 and 4096 MiB")
    common = f"""
    client_max_body_size {max_upload_mb}m;
    location /.well-known/acme-challenge/ {{
        root /var/lib/vps-panel/acme;
    }}
    location / {{
        proxy_pass http://127.0.0.1:{upstream_port};
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_read_timeout 300s;
    }}""".strip()
    if ssl_enabled:
        content = f"""server {{
    listen 80;
    listen [::]:80;
    server_name {hostname};
    location /.well-known/acme-challenge/ {{
        root /var/lib/vps-panel/acme;
    }}
    location / {{
        return 301 https://$host$request_uri;
    }}
}}
server {{
    listen 443 ssl http2;
    listen [::]:443 ssl http2;
    server_name {hostname};
    ssl_certificate /etc/letsencrypt/live/{hostname}/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/{hostname}/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_session_timeout 1d;
    add_header Strict-Transport-Security "max-age=31536000" always;
    {common}
}}
"""
    else:
        content = f"""server {{
    listen 80;
    listen [::]:80;
    server_name {hostname};
    {common}
}}
"""
    target = _write_nginx_config(hostname, content)
    return {"path": str(target), "ssl_enabled": ssl_enabled}


def configure_static_site(
    hostname: str,
    static_root: str,
    *,
    ssl_enabled: bool = False,
    spa_fallback: bool = True,
    max_upload_mb: int = 64,
) -> dict[str, Any]:
    """Generate an Nginx virtual host for static files under project storage."""
    _validate_hostname(hostname)
    if not static_root.startswith("projects/"):
        raise ValueError("Static root must belong to a project")
    resolved = safe_child(settings.storage_root, static_root)
    if not resolved.is_dir():
        raise ValueError("Static root does not exist")
    display_root = settings.storage_root / static_root.lstrip("/")
    if not 1 <= max_upload_mb <= 4096:
        raise ValueError("Upload limit must be between 1 and 4096 MiB")
    fallback = (
        "try_files $uri $uri/ /index.html;" if spa_fallback else "try_files $uri $uri/ =404;"
    )
    common = f"""
    root {display_root};
    index index.html;
    client_max_body_size {max_upload_mb}m;
    location /.well-known/acme-challenge/ {{
        root /var/lib/vps-panel/acme;
    }}
    location / {{
        {fallback}
    }}""".strip()
    if ssl_enabled:
        content = f"""server {{
    listen 80;
    listen [::]:80;
    server_name {hostname};
    location /.well-known/acme-challenge/ {{
        root /var/lib/vps-panel/acme;
    }}
    location / {{
        return 301 https://$host$request_uri;
    }}
}}
server {{
    listen 443 ssl http2;
    listen [::]:443 ssl http2;
    server_name {hostname};
    ssl_certificate /etc/letsencrypt/live/{hostname}/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/{hostname}/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_session_timeout 1d;
    add_header Strict-Transport-Security "max-age=31536000" always;
    {common}
}}
"""
    else:
        content = f"""server {{
    listen 80;
    listen [::]:80;
    server_name {hostname};
    {common}
}}
"""
    target = _write_nginx_config(hostname, content)
    return {"path": str(target), "ssl_enabled": ssl_enabled, "static_root": str(display_root)}


def remove_nginx_config(hostname: str) -> dict[str, Any]:
    """Remove one validated project virtual host configuration."""
    _validate_hostname(hostname)
    target = settings.nginx_config_root / f"{hostname}.conf"
    if target.exists() and not (target.is_file() or target.is_symlink()):
        raise ValueError("Nginx configuration is not a regular file")
    existed = target.exists() or target.is_symlink()
    target.unlink(missing_ok=True)
    return {"removed": str(target), "changed": existed}


async def validate_nginx() -> dict[str, Any]:
    """Validate the complete Nginx configuration."""
    return await run_command([str(settings.nginx_binary), "-t"])


async def reload_nginx() -> dict[str, Any]:
    """Reload Nginx through systemd after external validation."""
    return await run_command([str(settings.systemctl_binary), "reload", "nginx.service"])


async def issue_certificate(hostname: str, email: str) -> dict[str, Any]:
    """Issue a certificate through the ACME webroot without shell expansion."""
    _validate_hostname(hostname)
    if not EMAIL_PATTERN.fullmatch(email):
        raise ValueError("Invalid certificate email")
    webroot = Path("/var/lib/vps-panel/acme")
    webroot.mkdir(parents=True, exist_ok=True)
    return await run_command(
        [
            str(settings.certbot_binary),
            "certonly",
            "--webroot",
            "--webroot-path",
            str(webroot),
            "--domain",
            hostname,
            "--email",
            email,
            "--agree-tos",
            "--non-interactive",
            "--keep-until-expiring",
        ],
        command_timeout=300,
    )


async def certificate_info(hostname: str) -> dict[str, Any]:
    """Read expiry and SHA-256 fingerprint for one managed certificate."""
    _validate_hostname(hostname)
    certificate = safe_child(settings.letsencrypt_root, f"live/{hostname}/cert.pem")
    if not certificate.is_file():
        raise ValueError("Managed certificate does not exist")
    result = await run_command(
        [
            str(settings.openssl_binary),
            "x509",
            "-in",
            str(certificate),
            "-noout",
            "-enddate",
            "-fingerprint",
            "-sha256",
        ]
    )
    expires_at: datetime | None = None
    fingerprint: str | None = None
    for line in str(result["stdout"]).splitlines():
        if line.startswith("notAfter="):
            raw_expiry = line.partition("=")[2].strip()
            try:
                expires_at = datetime.strptime(
                    raw_expiry,
                    "%b %d %H:%M:%S %Y %Z",
                ).replace(tzinfo=UTC)
            except ValueError as exc:
                raise CommandError("OpenSSL returned an invalid expiry date") from exc
        elif "Fingerprint=" in line:
            raw_fingerprint = line.partition("=")[2].replace(":", "").strip().lower()
            if not re.fullmatch(r"[a-f0-9]{64}", raw_fingerprint):
                raise CommandError("OpenSSL returned an invalid certificate fingerprint")
            fingerprint = raw_fingerprint
    if expires_at is None or fingerprint is None:
        raise CommandError("OpenSSL did not return complete certificate metadata")
    return {
        "hostname": hostname,
        "expires_at": expires_at.isoformat(),
        "fingerprint_sha256": fingerprint,
    }


async def renew_certificate(hostname: str, *, force: bool = False) -> dict[str, Any]:
    """Renew one exact managed certificate through Certbot."""
    _validate_hostname(hostname)
    renewal = safe_child(settings.letsencrypt_root, f"renewal/{hostname}.conf")
    if not renewal.is_file():
        raise ValueError("Managed certificate does not exist")
    arguments = [
        str(settings.certbot_binary),
        "renew",
        "--cert-name",
        hostname,
        "--non-interactive",
    ]
    if force:
        arguments.append("--force-renewal")
    return await run_command(arguments, command_timeout=600)


async def remove_certificate(hostname: str) -> dict[str, Any]:
    """Delete one exact Certbot certificate when managed state exists."""
    _validate_hostname(hostname)
    renewal = safe_child(settings.letsencrypt_root, f"renewal/{hostname}.conf")
    if not renewal.is_file():
        return {"hostname": hostname, "deleted": False}
    result = await run_command(
        [
            str(settings.certbot_binary),
            "delete",
            "--cert-name",
            hostname,
            "--non-interactive",
        ],
        command_timeout=300,
    )
    return {
        "hostname": hostname,
        "deleted": True,
        "output": result["stdout"][-4096:],
    }


async def renew_certificates() -> dict[str, Any]:
    """Renew eligible certificates using existing Certbot state."""
    return await run_command(
        [str(settings.certbot_binary), "renew", "--non-interactive", "--quiet"],
        command_timeout=600,
    )


async def manage_service(service: str, action: str) -> dict[str, Any]:
    """Start, stop, restart, or inspect an allowlisted project unit."""
    if not SERVICE_PATTERN.fullmatch(service):
        raise ValueError("Invalid project service name")
    if action not in {"start", "stop", "restart", "status"}:
        raise ValueError("Unsupported service action")
    verb = "is-active" if action == "status" else action
    return await run_command([str(settings.systemctl_binary), verb, service])


async def service_logs(service: str, lines: int = 500) -> dict[str, Any]:
    """Read a bounded journal tail for one allowlisted project unit."""
    if not SERVICE_PATTERN.fullmatch(service):
        raise ValueError("Invalid project service name")
    if not 1 <= lines <= 5000:
        raise ValueError("Log line count must be between 1 and 5000")
    return await run_command(
        [
            "/usr/bin/journalctl",
            "--unit",
            service,
            "--no-pager",
            "--output",
            "short-iso",
            "--lines",
            str(lines),
        ],
        command_timeout=30,
    )


def _validate_database_identifier(value: str) -> str:
    """Validate a local database identifier passed as a CLI argument."""
    if not DATABASE_IDENTIFIER_PATTERN.fullmatch(value):
        raise ValueError("Invalid database identifier")
    return value


def _mysql_binary() -> Path:
    """Return the preferred local MySQL-compatible client binary."""
    if settings.mariadb_binary.is_file():
        return settings.mariadb_binary
    return settings.mysql_binary


async def database_server_status(engine: str) -> dict[str, Any]:
    """Inspect a local socket database server through root-owned CLI clients."""
    if engine == "postgresql":
        result = await run_command(
            [
                str(settings.runuser_binary),
                "-u",
                "postgres",
                "--",
                str(settings.psql_binary),
                "--no-align",
                "--tuples-only",
                "--dbname",
                "postgres",
                "--command",
                (
                    "SHOW server_version; "
                    "SELECT count(*) FROM pg_database WHERE datistemplate = false;"
                ),
            ],
            command_timeout=30,
        )
        lines = [line.strip() for line in str(result["stdout"]).splitlines() if line.strip()]
        return {
            "engine": engine,
            "status": "ready",
            "version": lines[0] if lines else None,
            "details": {"database_count": int(lines[1]) if len(lines) > 1 else 0},
        }
    if engine in {"mysql", "mariadb"}:
        result = await run_command(
            [
                str(_mysql_binary()),
                "--batch",
                "--skip-column-names",
                "--execute",
                "SELECT VERSION(); SELECT COUNT(*) FROM information_schema.schemata;",
            ],
            command_timeout=30,
        )
        lines = [line.strip() for line in str(result["stdout"]).splitlines() if line.strip()]
        return {
            "engine": engine,
            "status": "ready",
            "version": lines[0] if lines else None,
            "details": {"database_count": int(lines[1]) if len(lines) > 1 else 0},
        }
    raise ValueError("Unsupported local database engine")


async def database_tables(
    engine: str,
    database: str,
    *,
    limit: int = 500,
) -> dict[str, Any]:
    """List local database tables through root-owned CLI clients."""
    if not 1 <= limit <= 1000:
        raise ValueError("Table list limit must be between 1 and 1000")
    database_name = _validate_database_identifier(database)
    if engine == "postgresql":
        result = await run_command(
            [
                str(settings.runuser_binary),
                "-u",
                "postgres",
                "--",
                str(settings.psql_binary),
                "--no-align",
                "--tuples-only",
                "--field-separator",
                "\t",
                "--dbname",
                database_name,
                "--command",
                (
                    "SELECT table_schema, table_name "
                    "FROM information_schema.tables "
                    "WHERE table_type = 'BASE TABLE' "
                    "AND table_schema NOT IN ('pg_catalog', 'information_schema') "
                    "ORDER BY table_schema, table_name "
                    "LIMIT 1000;"
                ),
            ],
            command_timeout=30,
            output_limit=1024 * 1024,
        )
        tables = []
        for line in str(result["stdout"]).splitlines():
            schema, separator, name = line.partition("\t")
            if separator and schema and name:
                tables.append({"schema": schema, "name": name, "table_type": "table"})
        return {"tables": tables[:limit]}
    if engine in {"mysql", "mariadb"}:
        result = await run_command(
            [
                str(_mysql_binary()),
                "--batch",
                "--skip-column-names",
                "--database",
                database_name,
                "--execute",
                (
                    "SELECT table_name "
                    "FROM information_schema.tables "
                    "WHERE table_schema = DATABASE() AND table_type = 'BASE TABLE' "
                    "ORDER BY table_name LIMIT 1000;"
                ),
            ],
            command_timeout=30,
            output_limit=1024 * 1024,
        )
        tables = [
            {"schema": database_name, "name": line.strip(), "table_type": "table"}
            for line in str(result["stdout"]).splitlines()
            if line.strip()
        ]
        return {"tables": tables[:limit]}
    raise ValueError("Unsupported local database engine")


async def manage_container(container: str, action: str) -> dict[str, Any]:
    """Start, stop, restart, or inspect an allowlisted Docker container."""
    if not CONTAINER_PATTERN.fullmatch(container):
        raise ValueError("Invalid project container name")
    if action not in {"start", "stop", "restart", "inspect"}:
        raise ValueError("Unsupported container action")
    return await run_command(
        [str(settings.docker_binary), action, container],
        command_timeout=120,
    )


def _compose_file_for_project(project_id: str) -> Path:
    """Resolve the current Docker Compose file for a project."""
    if not PROJECT_ID_PATTERN.fullmatch(project_id):
        raise ValueError("Invalid project ID")
    current = safe_child(settings.storage_root, f"projects/{project_id}/current")
    if not current.is_dir():
        raise ValueError("Project current release does not exist")
    compose_file = current / "docker-compose.yml"
    if not compose_file.is_file():
        compose_file = current / "compose.yml"
    if not compose_file.is_file():
        raise ValueError("docker-compose.yml or compose.yml is required")
    return compose_file


async def manage_compose(project_id: str, action: str) -> dict[str, Any]:
    """Start, stop, restart, or inspect an allowlisted Docker Compose project."""
    if action not in {"start", "stop", "restart", "ps"}:
        raise ValueError("Unsupported compose action")
    compose_file = _compose_file_for_project(project_id)
    compose_action = "ps" if action == "ps" else action
    return await run_command(
        [
            str(settings.docker_binary),
            "compose",
            "--project-name",
            f"vps-project-{project_id}",
            "--project-directory",
            str(compose_file.parent),
            "--file",
            str(compose_file),
            compose_action,
        ],
        command_timeout=180,
    )


def _validated_docker_resources(output: str) -> list[str]:
    """Parse bounded Docker resource identifiers returned by the local daemon."""
    resources = list(dict.fromkeys(output.split()))
    if any(not DOCKER_RESOURCE_PATTERN.fullmatch(item) for item in resources):
        raise CommandError("Docker returned an invalid resource identifier")
    return resources


async def _remove_labeled_docker_resources(
    resource: str,
    *,
    label: str,
    remove_arguments: list[str],
) -> int:
    """Remove Docker resources selected only by an exact project label."""
    result = await run_command(
        [
            str(settings.docker_binary),
            resource,
            "ls",
            "--quiet",
            "--filter",
            f"label={label}",
        ],
        command_timeout=120,
    )
    resources = _validated_docker_resources(str(result["stdout"]))
    if resources:
        await run_command(
            [str(settings.docker_binary), resource, *remove_arguments, *resources],
            command_timeout=300,
        )
    return len(resources)


def _project_service_path(project_id: str) -> tuple[str, Path]:
    """Return the fixed service name and contained unit path for a project."""
    if not PROJECT_ID_PATTERN.fullmatch(project_id):
        raise ValueError("Invalid project ID")
    service = f"vps-project-{project_id}.service"
    return service, safe_child(settings.systemd_unit_root, service)


def _project_runtime_username(project_id: str) -> str:
    """Derive a deterministic Linux username for one project runtime."""
    if not PROJECT_ID_PATTERN.fullmatch(project_id):
        raise ValueError("Invalid project ID")
    prefix = settings.project_runtime_user_prefix
    if not LINUX_NAME_PATTERN.fullmatch(prefix) or len(prefix) > 8:
        raise ValueError("Invalid project runtime username prefix")
    digest = hashlib.sha256(project_id.encode("ascii")).hexdigest()
    return f"{prefix}{digest[: 32 - len(prefix)]}"


async def _ensure_project_runtime_user(project_id: str) -> str:
    """Create the deterministic unprivileged Linux user for one project."""
    username = _project_runtime_username(project_id)
    if not LINUX_NAME_PATTERN.fullmatch(settings.project_runtime_group):
        raise ValueError("Invalid project runtime group")
    try:
        pwd.getpwnam(username)
        return username
    except KeyError:
        pass
    try:
        await run_command(
            [
                str(settings.useradd_binary),
                "--system",
                "--no-create-home",
                "--gid",
                settings.project_runtime_group,
                "--shell",
                "/usr/sbin/nologin",
                username,
            ],
            command_timeout=60,
        )
    except CommandError:
        with contextlib.suppress(KeyError):
            pwd.getpwnam(username)
            return username
        raise
    return username


async def _remove_project_runtime_user(project_id: str) -> bool:
    """Remove the deterministic runtime user if it exists."""
    username = _project_runtime_username(project_id)
    try:
        pwd.getpwnam(username)
    except KeyError:
        return False
    await run_command([str(settings.userdel_binary), username], command_timeout=60)
    return True


def _assert_managed_unit(path: Path, project_id: str) -> None:
    """Reject deletion of a fixed-name unit not created for this project."""
    if not path.exists():
        return
    if path.is_symlink() or not path.is_file():
        raise ValueError("Project service unit is not a regular file")
    marker = f"# Managed by NectarinePanel for project {project_id}\n"
    with path.open(encoding="utf-8") as stream:
        if stream.readline() != marker:
            raise ValueError("Project service unit is not managed by NectarinePanel")


async def _remove_project_service(project_id: str) -> bool:
    """Disable and remove one verified project systemd unit."""
    service, unit_path = _project_service_path(project_id)
    _assert_managed_unit(unit_path, project_id)
    if not unit_path.exists():
        return False
    await run_command(
        [str(settings.systemctl_binary), "disable", "--now", service],
        command_timeout=120,
    )
    unit_path.unlink()
    await run_command([str(settings.systemctl_binary), "daemon-reload"])
    return True


async def _remove_project_container(project_id: str) -> bool:
    """Remove a fixed-name container after verifying its ownership label."""
    container = f"vps-project-{project_id}"
    try:
        result = await run_command(
            [
                str(settings.docker_binary),
                "container",
                "inspect",
                "--format",
                '{{ index .Config.Labels "io.nectarine.project" }}',
                container,
            ],
            command_timeout=60,
        )
    except CommandError:
        return False
    if str(result["stdout"]).strip() != project_id:
        raise ValueError("Project container has an unexpected ownership label")
    await run_command(
        [str(settings.docker_binary), "container", "rm", "--force", container],
        command_timeout=180,
    )
    return True


async def _remove_compose_project(project_id: str) -> dict[str, int | bool]:
    """Remove one fixed Docker Compose project and its labeled resources."""
    compose_project = f"vps-project-{project_id}"
    used_compose_file = False
    try:
        compose_file = _compose_file_for_project(project_id)
    except ValueError:
        compose_file = None
    if compose_file is not None:
        await run_command(
            [
                str(settings.docker_binary),
                "compose",
                "--project-name",
                compose_project,
                "--project-directory",
                str(compose_file.parent),
                "--file",
                str(compose_file),
                "down",
                "--remove-orphans",
                "--volumes",
                "--rmi",
                "local",
                "--timeout",
                "30",
            ],
            command_timeout=600,
        )
        used_compose_file = True
    label = f"com.docker.compose.project={compose_project}"
    containers = await _remove_labeled_docker_resources(
        "container",
        label=label,
        remove_arguments=["rm", "--force"],
    )
    networks = await _remove_labeled_docker_resources(
        "network",
        label=label,
        remove_arguments=["rm"],
    )
    volumes = await _remove_labeled_docker_resources(
        "volume",
        label=label,
        remove_arguments=["rm", "--force"],
    )
    images = await _remove_labeled_docker_resources(
        "image",
        label=label,
        remove_arguments=["rm", "--force"],
    )
    return {
        "compose_file": used_compose_file,
        "containers": containers,
        "networks": networks,
        "volumes": volumes,
        "images": images,
    }


async def _remove_project_images(project_id: str) -> int:
    """Remove images built and labeled for one exact project."""
    return await _remove_labeled_docker_resources(
        "image",
        label=f"io.nectarine.project={project_id}",
        remove_arguments=["rm", "--force"],
    )


async def compose_logs(project_id: str, lines: int = 500) -> dict[str, Any]:
    """Read a bounded Docker Compose log tail for one project."""
    if not 1 <= lines <= 5000:
        raise ValueError("Log line count must be between 1 and 5000")
    compose_file = _compose_file_for_project(project_id)
    return await run_command(
        [
            str(settings.docker_binary),
            "compose",
            "--project-name",
            f"vps-project-{project_id}",
            "--project-directory",
            str(compose_file.parent),
            "--file",
            str(compose_file),
            "logs",
            "--no-color",
            "--tail",
            str(lines),
        ],
        command_timeout=60,
    )


async def compose_command(project_id: str, service: str, command: str) -> dict[str, Any]:
    """Run a bounded command inside one allowlisted Docker Compose service."""
    if not PROJECT_ID_PATTERN.fullmatch(project_id):
        raise ValueError("Invalid project ID")
    if not COMPOSE_SERVICE_PATTERN.fullmatch(service):
        raise ValueError("Invalid compose service")
    if not command.strip() or len(command) > 4096 or "\x00" in command:
        raise ValueError("Invalid compose command")
    compose_file = _compose_file_for_project(project_id)
    return await run_command(
        [
            str(settings.docker_binary),
            "compose",
            "--project-name",
            f"vps-project-{project_id}",
            "--project-directory",
            str(compose_file.parent),
            "--file",
            str(compose_file),
            "exec",
            "-T",
            service,
            "/bin/sh",
            "-lc",
            command,
        ],
        command_timeout=120,
    )


async def container_logs(container: str, lines: int = 500) -> dict[str, Any]:
    """Read a bounded tail of one allowlisted project container."""
    if not CONTAINER_PATTERN.fullmatch(container):
        raise ValueError("Invalid project container name")
    if not 1 <= lines <= 5000:
        raise ValueError("Log line count must be between 1 and 5000")
    return await run_command(
        [
            str(settings.docker_binary),
            "logs",
            "--timestamps",
            "--tail",
            str(lines),
            container,
        ],
        command_timeout=30,
    )


async def container_command(container: str, command: str) -> dict[str, Any]:
    """Run a bounded command inside an allowlisted project container."""
    if not CONTAINER_PATTERN.fullmatch(container):
        raise ValueError("Invalid project container name")
    if not command.strip() or len(command) > 4096 or "\x00" in command:
        raise ValueError("Invalid container command")
    return await run_command(
        [
            str(settings.docker_binary),
            "exec",
            container,
            "/bin/sh",
            "-lc",
            command,
        ],
        command_timeout=120,
    )


def _project_release(project_id: str, release_id: str) -> Path:
    """Resolve a validated immutable project release directory."""
    if not PROJECT_ID_PATTERN.fullmatch(project_id):
        raise ValueError("Invalid project ID")
    if not RELEASE_ID_PATTERN.fullmatch(release_id):
        raise ValueError("Invalid release ID")
    root = safe_child(settings.storage_root, f"projects/{project_id}")
    release = safe_child(root, f"releases/{release_id}")
    if not release.is_dir():
        raise ValueError("Project release directory does not exist")
    return release


def _project_env_file(project_id: str, env_file: str | None) -> Path | None:
    """Resolve an optional runtime env file under the target project root."""
    if not env_file:
        return None
    env_path = safe_child(settings.storage_root, env_file)
    project_root = safe_child(settings.storage_root, f"projects/{project_id}")
    if project_root not in env_path.parents or not env_path.is_file():
        raise ValueError("Invalid project environment file")
    return env_path


def _env_file_directives(env_path: Path | None) -> list[str]:
    """Render systemd Environment directives from a validated env file."""
    if env_path is None:
        return []
    directives: list[str] = []
    for line in env_path.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if not separator or not SERVICE_ENV_KEY_PATTERN.fullmatch(key):
            raise ValueError("Invalid runtime environment file")
        escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%")
        directives.append(f'Environment="{key}={escaped}"')
    return directives


def _env_file_values(env_path: Path | None) -> dict[str, str]:
    """Load validated single-line environment values for a project command."""
    if env_path is None:
        return {}
    values: dict[str, str] = {}
    for line in env_path.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if not separator or not SERVICE_ENV_KEY_PATTERN.fullmatch(key) or "\x00" in value:
            raise ValueError("Invalid runtime environment file")
        values[key] = value
    return values


def _validate_service_command(command: str) -> str:
    """Validate a project-owned command executed by the service shell."""
    normalized = command.strip()
    if not normalized or len(normalized) > 4000:
        raise ValueError("Invalid runtime start command")
    if "\x00" in normalized or "\n" in normalized or "\r" in normalized:
        raise ValueError("Invalid runtime start command")
    return normalized


def _validate_cpu_cores(cpu_cores: float | None) -> float | None:
    """Validate optional CPU core allocation."""
    if cpu_cores is None:
        return None
    value = float(cpu_cores)
    if not 0.1 <= value <= 128:
        raise ValueError("CPU limit must be between 0.1 and 128 cores")
    return value


def _validate_memory_mb(memory_mb: int | None) -> int | None:
    """Validate optional memory allocation in MiB."""
    if memory_mb is None:
        return None
    value = int(memory_mb)
    if not 64 <= value <= 1_048_576:
        raise ValueError("Memory limit must be between 64 and 1048576 MiB")
    return value


def _docker_resource_arguments(
    *,
    cpu_cores: float | None,
    memory_mb: int | None,
) -> list[str]:
    """Return validated Docker resource limit arguments."""
    cpu_limit = _validate_cpu_cores(cpu_cores)
    memory_limit = _validate_memory_mb(memory_mb)
    arguments: list[str] = []
    if cpu_limit is not None:
        arguments.extend(["--cpus", f"{cpu_limit:.3f}".rstrip("0").rstrip(".")])
    if memory_limit is not None:
        arguments.extend(["--memory", f"{memory_limit}m"])
    return arguments


def _docker_persistent_mount_arguments(
    project_id: str,
    mounts: list[dict[str, Any]] | None,
) -> tuple[list[str], list[dict[str, Any]]]:
    """Create validated project-owned bind mount arguments."""
    if mounts is None:
        return [], []
    if not isinstance(mounts, list) or len(mounts) > 16:
        raise ValueError("Persistent mounts must be a list with at most 16 entries")
    project_root = safe_child(settings.storage_root, f"projects/{project_id}")
    shared_root = safe_child(project_root, "shared")
    shared_root.mkdir(parents=True, exist_ok=True)
    owner = project_root.stat()
    arguments = ["--group-add", str(owner.st_gid)] if mounts else []
    normalized: list[dict[str, Any]] = []
    targets: set[str] = set()
    for mount in mounts:
        if not isinstance(mount, dict) or set(mount) - {"source", "target", "read_only"}:
            raise ValueError("Invalid persistent mount")
        source = mount.get("source")
        target = mount.get("target")
        read_only = mount.get("read_only", False)
        if (
            not isinstance(source, str)
            or len(source) > 512
            or not DOCKER_MOUNT_SOURCE_PATTERN.fullmatch(source)
            or not isinstance(target, str)
            or len(target) > 512
            or not DOCKER_MOUNT_TARGET_PATTERN.fullmatch(target)
            or not isinstance(read_only, bool)
        ):
            raise ValueError("Invalid persistent mount")
        if target in targets:
            raise ValueError("Persistent mount targets must be unique")
        targets.add(target)
        host_path = shared_root
        for part in source.split("/"):
            host_path /= part
            if host_path.is_symlink():
                raise ValueError("Persistent mount source cannot be a symlink")
            if host_path.exists() and not host_path.is_dir():
                raise ValueError("Persistent mount source must be a directory")
            host_path.mkdir(exist_ok=True)
            os.chown(host_path, owner.st_uid, owner.st_gid)
            os.chmod(host_path, 0o2770)  # noqa: S103
        option = f"type=bind,source={host_path},target={target}"
        if read_only:
            option += ",readonly"
        arguments.extend(["--mount", option])
        normalized.append(
            {
                "source": source,
                "target": target,
                "read_only": read_only,
            }
        )
    return arguments, normalized


def _persistent_mount_host_path(project_id: str, source: str) -> Path:
    """Return the validated host directory backing a project mount."""
    return safe_child(
        settings.storage_root,
        f"projects/{project_id}/shared/{source}",
    )


def _normalize_persistent_data_permissions(path: Path, owner: os.stat_result) -> None:
    """Make imported container data writable only by the project owner and group."""
    for item in (path, *path.rglob("*")):
        try:
            metadata = item.lstat()
            os.chown(item, owner.st_uid, owner.st_gid, follow_symlinks=False)
            if stat.S_ISLNK(metadata.st_mode):
                continue
            mode = stat.S_IMODE(metadata.st_mode) & ~0o007
            if stat.S_ISDIR(metadata.st_mode):
                mode |= 0o2770
            elif stat.S_ISREG(metadata.st_mode):
                mode |= 0o660
                if metadata.st_mode & 0o100:
                    mode |= 0o010
            os.chmod(item, mode, follow_symlinks=False)
        except FileNotFoundError:
            continue


async def _migrate_container_persistent_data(
    container: str,
    project_id: str,
    mounts: list[dict[str, Any]],
) -> list[dict[str, str]]:
    """Copy previously ephemeral mount targets from an old container once."""
    project_root = safe_child(settings.storage_root, f"projects/{project_id}")
    owner = project_root.stat()
    migrated: list[dict[str, str]] = []
    for mount in mounts:
        source = str(mount["source"])
        target = str(mount["target"])
        host_path = _persistent_mount_host_path(project_id, source)
        if any(host_path.iterdir()):
            continue
        with tempfile.TemporaryDirectory(
            prefix=".container-data-",
            dir=host_path.parent,
        ) as temporary:
            staging = Path(temporary)
            try:
                await run_command(
                    [
                        str(settings.docker_binary),
                        "container",
                        "cp",
                        f"{container}:{target}/.",
                        str(staging),
                    ],
                    command_timeout=300,
                )
            except CommandError as exc:
                message = str(exc).lower()
                if "could not find" in message or "no such file or directory" in message:
                    continue
                raise
            for item in staging.iterdir():
                destination = host_path / item.name
                if destination.exists() or destination.is_symlink():
                    raise CommandError("Persistent data destination changed during migration")
                item.replace(destination)
        _normalize_persistent_data_permissions(host_path, owner)
        migrated.append({"source": source, "target": target})
    return migrated


def _loopback_port_available(port: int) -> bool:
    """Return whether a TCP port can be bound on the loopback interface."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as candidate:
        try:
            candidate.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def _docker_assigned_host_port(output: str) -> int:
    """Parse the loopback host port reported by Docker."""
    match = re.fullmatch(r"127\.0\.0\.1:(\d{1,5})", output.strip())
    if match is None:
        raise CommandError("Docker returned an invalid published port")
    port = int(match.group(1))
    if not 1024 <= port <= 65535:
        raise CommandError("Docker returned an invalid published port")
    return port


def _systemd_resource_directives(
    *,
    cpu_cores: float | None,
    memory_mb: int | None,
) -> list[str]:
    """Return validated systemd resource limit directives."""
    cpu_limit = _validate_cpu_cores(cpu_cores)
    memory_limit = _validate_memory_mb(memory_mb)
    directives: list[str] = []
    if cpu_limit is not None:
        directives.append(f"CPUQuota={cpu_limit * 100:.1f}%")
    if memory_limit is not None:
        directives.append(f"MemoryMax={memory_limit}M")
    return directives


def _systemd_shell_argument(command: str) -> str:
    """Quote one exact systemd argument and escape specifier expansion."""
    escaped = command.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%")
    return f'"{escaped}"'


async def run_project_command(
    project_id: str,
    command: str,
    *,
    env_file: str | None = None,
) -> dict[str, Any]:
    """Run a bounded command as the project's unprivileged runtime user."""
    if not PROJECT_ID_PATTERN.fullmatch(project_id):
        raise ValueError("Invalid project ID")
    normalized = _validate_service_command(command)
    release = safe_child(settings.storage_root, f"projects/{project_id}/current")
    if not release.is_dir():
        raise ValueError("Project current release does not exist")
    resolved_env = _project_env_file(project_id, env_file) if env_file else None
    if resolved_env is None:
        default_env = safe_child(
            settings.storage_root, f"projects/{project_id}/shared/runtime.env"
        )
        if default_env.is_file():
            resolved_env = default_env
    runtime_user = _project_runtime_username(project_id)
    return await run_command(
        [
            str(settings.runuser_binary),
            "-u",
            runtime_user,
            "--",
            "/bin/sh",
            "-lc",
            normalized,
        ],
        command_timeout=120,
        cwd=release,
        env_overrides=_env_file_values(resolved_env),
    )


async def _write_project_service(
    project_id: str,
    release_id: str,
    *,
    start_command: str,
    env_file: str | None = None,
    extra_environment: dict[str, str] | None = None,
    cpu_cores: float | None = None,
    memory_mb: int | None = None,
    description: str,
) -> dict[str, Any]:
    """Write and restart a bounded systemd unit for a project release."""
    release = _project_release(project_id, release_id)
    command = _validate_service_command(start_command)
    env_path = _project_env_file(project_id, env_file)
    runtime_user = await _ensure_project_runtime_user(project_id)
    service = f"vps-project-{project_id}.service"
    unit_path = safe_child(settings.systemd_unit_root, service)
    log_root = safe_child(settings.storage_root, f"projects/{project_id}/logs")
    log_root.mkdir(parents=True, exist_ok=True)
    shutil.chown(log_root, user=runtime_user, group=settings.project_runtime_group)
    os.chmod(log_root, 0o750)  # noqa: S103
    environment = _env_file_directives(env_path)
    for key, value in (extra_environment or {}).items():
        if not SERVICE_ENV_KEY_PATTERN.fullmatch(key) or any(
            character in value for character in "\r\n\x00"
        ):
            raise ValueError("Invalid runtime environment override")
        escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%")
        environment.append(f'Environment="{key}={escaped}"')
    unit_path.parent.mkdir(parents=True, exist_ok=True)
    content = "\n".join(
        [
            f"# Managed by NectarinePanel for project {project_id}",
            "[Unit]",
            f"Description={description}",
            "After=network-online.target",
            "Wants=network-online.target",
            "",
            "[Service]",
            "Type=simple",
            f"User={runtime_user}",
            f"Group={settings.project_runtime_group}",
            "UMask=0027",
            f"WorkingDirectory={release}",
            *environment,
            f"ExecStart=/bin/sh -lc {_systemd_shell_argument(command)}",
            "Restart=on-failure",
            "RestartSec=5",
            "NoNewPrivileges=true",
            "PrivateTmp=true",
            "ProtectSystem=full",
            "ProtectHome=true",
            *_systemd_resource_directives(cpu_cores=cpu_cores, memory_mb=memory_mb),
            f"ReadWritePaths={release} {log_root}",
            "KillSignal=SIGTERM",
            "TimeoutStopSec=30",
            "",
            "[Install]",
            "WantedBy=multi-user.target",
            "",
        ]
    )
    unit_path.write_text(content, encoding="utf-8")
    os.chmod(unit_path, 0o640)
    await run_command([str(settings.systemctl_binary), "daemon-reload"], command_timeout=60)
    await run_command(
        [str(settings.systemctl_binary), "enable", "--now", service],
        command_timeout=120,
    )
    await run_command([str(settings.systemctl_binary), "restart", service], command_timeout=120)
    return {"service": service, "unit_path": str(unit_path)}


async def deploy_systemd(
    project_id: str,
    release_id: str,
    *,
    start_command: str,
    env_file: str | None = None,
    cpu_cores: float | None = None,
    memory_mb: int | None = None,
) -> dict[str, Any]:
    """Deploy a project as a restricted systemd service."""
    return await _write_project_service(
        project_id,
        release_id,
        start_command=start_command,
        env_file=env_file,
        cpu_cores=cpu_cores,
        memory_mb=memory_mb,
        description=f"NectarinePanel project {project_id}",
    )


async def deploy_pm2(
    project_id: str,
    release_id: str,
    *,
    start_command: str,
    env_file: str | None = None,
    cpu_cores: float | None = None,
    memory_mb: int | None = None,
) -> dict[str, Any]:
    """Deploy a Node project through a restricted PM2-backed systemd service."""
    command = _validate_service_command(start_command)
    runtime_user = await _ensure_project_runtime_user(project_id)
    pm2_home = safe_child(settings.storage_root, f"projects/{project_id}/logs/pm2")
    pm2_home.mkdir(parents=True, exist_ok=True)
    shutil.chown(pm2_home, user=runtime_user, group=settings.project_runtime_group)
    os.chmod(pm2_home, 0o750)  # noqa: S103
    pm2_command = (
        f"exec {shlex.quote(str(settings.pm2_runtime_binary))} /bin/sh "
        f"--name vps-project-{project_id} -- -lc {shlex.quote(command)}"
    )
    return await _write_project_service(
        project_id,
        release_id,
        start_command=pm2_command,
        env_file=env_file,
        extra_environment={"PM2_HOME": str(pm2_home)},
        cpu_cores=cpu_cores,
        memory_mb=memory_mb,
        description=f"NectarinePanel PM2 project {project_id}",
    )


async def deploy_container(
    project_id: str,
    release_id: str,
    *,
    internal_port: int,
    host_port: int = 0,
    start_command: str | None = None,
    env_file: str | None = None,
    persistent_mounts: list[dict[str, Any]] | None = None,
    cpu_cores: float | None = None,
    memory_mb: int | None = None,
) -> dict[str, Any]:
    """Build and replace a hardened project Docker container."""
    release = _project_release(project_id, release_id)
    if not (release / "Dockerfile").is_file():
        raise ValueError("Dockerfile is required for Docker runtime")
    if not 1 <= internal_port <= 65535 or (host_port != 0 and not 1024 <= host_port <= 65535):
        raise ValueError("Invalid container port mapping")
    command_override = _validate_service_command(start_command) if start_command else None
    mount_args, normalized_mounts = _docker_persistent_mount_arguments(
        project_id,
        persistent_mounts,
    )
    container = f"vps-project-{project_id}"
    image = f"{container}:{release_id.lower()}"
    env_args: list[str] = []
    if env_file:
        env_path = safe_child(settings.storage_root, env_file)
        project_root = safe_child(settings.storage_root, f"projects/{project_id}")
        if project_root not in env_path.parents or not env_path.is_file():
            raise ValueError("Invalid project environment file")
        env_args = ["--env-file", str(env_path)]
    build = await run_command(
        [
            str(settings.docker_binary),
            "build",
            "--pull",
            "--label",
            f"io.nectarine.project={project_id}",
            "--tag",
            image,
            str(release),
        ],
        command_timeout=1800,
    )
    inspect = await asyncio.create_subprocess_exec(
        str(settings.docker_binary),
        "container",
        "inspect",
        container,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    migrated_mounts: list[dict[str, str]] = []
    if await inspect.wait() == 0:
        try:
            await run_command(
                [str(settings.docker_binary), "container", "stop", "--time", "30", container],
                command_timeout=60,
            )
            migrated_mounts = await _migrate_container_persistent_data(
                container,
                project_id,
                normalized_mounts,
            )
            await run_command(
                [str(settings.docker_binary), "rm", "--force", container],
                command_timeout=120,
            )
        except (CommandError, OSError):
            with contextlib.suppress(CommandError):
                await run_command(
                    [str(settings.docker_binary), "container", "start", container],
                    command_timeout=60,
                )
            raise
    if host_port and not _loopback_port_available(host_port):
        raise ValueError(f"Docker host port {host_port} is already in use")
    publish = (
        f"127.0.0.1:{host_port}:{internal_port}" if host_port else f"127.0.0.1::{internal_port}"
    )
    arguments = [
        str(settings.docker_binary),
        "run",
        "--detach",
        "--name",
        container,
        "--restart",
        "unless-stopped",
        "--security-opt",
        "no-new-privileges:true",
        "--cap-drop",
        "ALL",
        "--label",
        f"io.nectarine.project={project_id}",
        "--publish",
        publish,
        *_docker_resource_arguments(cpu_cores=cpu_cores, memory_mb=memory_mb),
        *env_args,
        *mount_args,
        image,
    ]
    if command_override:
        arguments.extend(["/bin/sh", "-lc", command_override])
    try:
        run_result = await run_command(arguments, command_timeout=180)
        assigned_host_port = host_port
        if assigned_host_port == 0:
            port_result = await run_command(
                [
                    str(settings.docker_binary),
                    "container",
                    "port",
                    container,
                    f"{internal_port}/tcp",
                ],
                command_timeout=30,
            )
            assigned_host_port = _docker_assigned_host_port(str(port_result["stdout"]))
    except (CommandError, ValueError):
        with contextlib.suppress(CommandError):
            await run_command(
                [str(settings.docker_binary), "rm", "--force", container],
                command_timeout=120,
            )
        raise
    return {
        "container": container,
        "image": image,
        "build_log": build["stdout"][-16384:],
        "container_id": run_result["stdout"].strip(),
        "internal_port": internal_port,
        "host_port": assigned_host_port,
        "command_overridden": command_override is not None,
        "persistent_mounts": normalized_mounts,
        "migrated_mounts": migrated_mounts,
    }


async def deploy_compose(
    project_id: str,
    release_id: str,
    *,
    env_file: str | None = None,
) -> dict[str, Any]:
    """Deploy a Docker Compose release under a bounded project name."""
    release = _project_release(project_id, release_id)
    compose_file = release / "docker-compose.yml"
    if not compose_file.is_file():
        compose_file = release / "compose.yml"
    if not compose_file.is_file():
        raise ValueError("docker-compose.yml or compose.yml is required")
    env_args: list[str] = []
    if env_file:
        env_path = safe_child(settings.storage_root, env_file)
        project_root = safe_child(settings.storage_root, f"projects/{project_id}")
        if project_root not in env_path.parents or not env_path.is_file():
            raise ValueError("Invalid project environment file")
        env_args = ["--env-file", str(env_path)]
    result = await run_command(
        [
            str(settings.docker_binary),
            "compose",
            "--project-name",
            f"vps-project-{project_id}",
            "--project-directory",
            str(release),
            *env_args,
            "--file",
            str(compose_file),
            "up",
            "--detach",
            "--build",
            "--remove-orphans",
        ],
        command_timeout=1800,
    )
    return {
        "project": f"vps-project-{project_id}",
        "compose_file": str(compose_file),
        "log": result["stdout"][-16384:],
    }


def _memory_megabytes(value: str) -> int:
    """Parse a Java memory size and return MiB."""
    match = re.fullmatch(r"([1-9][0-9]{0,5})([MG])", value.upper())
    if not match:
        raise ValueError("Memory must use a numeric M or G suffix")
    amount = int(match.group(1))
    megabytes = amount * 1024 if match.group(2) == "G" else amount
    if megabytes < 256:
        raise ValueError("Minecraft memory must be at least 256 MiB")
    return megabytes


def _write_server_properties(
    root: Path,
    *,
    game_port: int,
    rcon_enabled: bool,
    rcon_port: int,
    rcon_password: str | None,
) -> None:
    """Update required server properties while preserving unrelated values."""
    path = root / "server.properties"
    properties: dict[str, str] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, value = line.split("=", 1)
                properties[key] = value
    properties["server-port"] = str(game_port)
    properties["enable-rcon"] = str(rcon_enabled).lower()
    properties["rcon.port"] = str(rcon_port)
    if rcon_enabled:
        if not rcon_password or any(character in rcon_password for character in "\r\n\x00"):
            raise ValueError("A valid RCON password is required")
        properties["rcon.password"] = rcon_password
        properties["broadcast-rcon-to-ops"] = "false"
    content = "\n".join(f"{key}={value}" for key, value in sorted(properties.items())) + "\n"
    path.write_text(content, encoding="utf-8")
    os.chmod(path, 0o640)


def _write_forge_jvm_arguments(root: Path, *, xms: str, xmx: str) -> None:
    """Persist managed memory limits while preserving unrelated Forge JVM options."""
    path = root / "user_jvm_args.txt"
    retained: list[str] = []
    if path.is_file() and not path.is_symlink():
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            stripped = line.strip().lower()
            if stripped.startswith(("-xms", "-xmx")):
                continue
            retained.append(line)
    content = [f"-Xms{xms.upper()}", f"-Xmx{xmx.upper()}", *retained]
    path.write_text("\n".join(content).rstrip() + "\n", encoding="utf-8")
    os.chmod(path, 0o640)


def _forge_download_path(minecraft_version: str, forge_version: str) -> str:
    """Build an official Forge installer path from validated version values."""
    if not FORGE_VERSION_PATTERN.fullmatch(
        minecraft_version
    ) or not FORGE_VERSION_PATTERN.fullmatch(forge_version):
        raise ValueError("Invalid Minecraft or Forge version")
    coordinate = f"{minecraft_version}-{forge_version}"
    return f"/net/minecraftforge/forge/{coordinate}/forge-{coordinate}-installer.jar"


def _open_forge_download(
    path: str,
    *,
    timeout: int = 120,
) -> tuple[HTTPSConnection, HTTPResponse]:
    """Open one HTTPS request to the fixed official Forge Maven host."""
    if not path.startswith("/net/minecraftforge/forge/") or ".." in path:
        raise ValueError("Invalid Forge download path")
    connection = HTTPSConnection(FORGE_MAVEN_HOST, timeout=timeout)
    connection.request("GET", path, headers={"User-Agent": "NectarinePanel/1.0"})
    response = connection.getresponse()
    if response.status != 200:
        response.close()
        connection.close()
        raise ValueError(f"Forge download failed with HTTP {response.status}")
    return connection, response


def _download_forge_installer(
    minecraft_version: str,
    forge_version: str,
    destination: Path,
) -> None:
    """Download an official Forge installer and verify its published checksum."""
    path = _forge_download_path(minecraft_version, forge_version)
    checksum_connection, checksum_response = _open_forge_download(
        f"{path}.sha1",
        timeout=30,
    )
    with contextlib.closing(checksum_connection), contextlib.closing(checksum_response):
        expected = checksum_response.read(128).decode("ascii", errors="strict").strip()
    if not re.fullmatch(r"[a-fA-F0-9]{40}", expected):
        raise ValueError("Forge installer checksum is invalid")
    temporary = destination.with_suffix(f"{destination.suffix}.part")
    temporary.unlink(missing_ok=True)
    digest = hashlib.sha1(usedforsecurity=False)
    written = 0
    try:
        connection, response = _open_forge_download(path)
        with (
            contextlib.closing(connection),
            contextlib.closing(response),
            temporary.open("xb") as output,
        ):
            declared_size = response.getheader("Content-Length")
            if declared_size is not None and int(declared_size) > FORGE_INSTALLER_MAX_BYTES:
                raise ValueError("Forge installer exceeds the size limit")
            while chunk := response.read(1024 * 1024):
                written += len(chunk)
                if written > FORGE_INSTALLER_MAX_BYTES:
                    raise ValueError("Forge installer exceeds the size limit")
                output.write(chunk)
                digest.update(chunk)
        if written == 0 or digest.hexdigest().lower() != expected.lower():
            raise ValueError("Forge installer checksum verification failed")
        temporary.replace(destination)
        os.chmod(destination, 0o640)
    finally:
        temporary.unlink(missing_ok=True)


async def install_minecraft(
    project_id: str,
    *,
    java_version: int,
    installer_jar: str | None = None,
    minecraft_version: str | None = None,
    forge_version: str | None = None,
) -> dict[str, Any]:
    """Download or run a Forge installer in a restricted temporary JDK container."""
    if not PROJECT_ID_PATTERN.fullmatch(project_id):
        raise ValueError("Invalid project ID")
    if java_version not in {8, 11, 16, 17, 21, 25}:
        raise ValueError("Unsupported Java version")
    automatic = minecraft_version is not None or forge_version is not None
    if automatic:
        if minecraft_version is None or forge_version is None or installer_jar is not None:
            raise ValueError("Both Minecraft and Forge versions are required")
        installer_jar = f"forge-{minecraft_version}-{forge_version}-installer.jar"
    elif installer_jar is None:
        raise ValueError("Forge installer source is required")
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,255}\.jar", installer_jar):
        raise ValueError("Invalid Forge installer JAR name")
    root = safe_child(settings.storage_root, f"minecraft/{project_id}")
    root.mkdir(parents=True, exist_ok=True)
    owner = root.stat()
    installer = safe_child(root, installer_jar)
    if automatic:
        assert minecraft_version is not None and forge_version is not None
        await asyncio.to_thread(
            _download_forge_installer,
            minecraft_version,
            forge_version,
            installer,
        )
    if (
        not installer.is_file()
        or installer.is_symlink()
        or installer.stat().st_size > FORGE_INSTALLER_MAX_BYTES
    ):
        raise ValueError("Forge installer JAR does not exist or is invalid")
    os.chown(installer, owner.st_uid, owner.st_gid)
    os.chmod(installer, 0o640)
    try:
        result = await run_command(
            [
                str(settings.docker_binary),
                "run",
                "--rm",
                "--security-opt",
                "no-new-privileges:true",
                "--cap-drop",
                "ALL",
                "--user",
                f"{owner.st_uid}:{owner.st_gid}",
                "--env",
                "HOME=/tmp",
                "--volume",
                f"{root}:/server",
                "--workdir",
                "/server",
                f"eclipse-temurin:{java_version}-jdk",
                "java",
                "-jar",
                installer_jar,
                "--installServer",
            ],
            command_timeout=1800,
        )
        script = root / "run.sh"
        if script.is_file() and not script.is_symlink():
            launcher = "run.sh"
            launch_mode = "forge_script"
        else:
            candidates = sorted(
                (
                    path
                    for path in root.glob("forge-*.jar")
                    if path.is_file()
                    and not path.is_symlink()
                    and path.name != installer_jar
                    and not path.name.endswith("-installer.jar")
                ),
                key=lambda path: path.stat().st_mtime_ns,
                reverse=True,
            )
            if not candidates:
                raise CommandError(
                    "Forge installer completed without a runnable server launcher"
                )
            launcher = candidates[0].name
            launch_mode = "jar"
        return {
            "project_id": project_id,
            "launcher": launcher,
            "launch_mode": launch_mode,
            "java_version": java_version,
            "minecraft_version": minecraft_version,
            "forge_version": forge_version,
            "log": (str(result["stdout"]) + "\n" + str(result["stderr"]))[-32768:],
        }
    finally:
        if automatic:
            installer.unlink(missing_ok=True)


async def start_minecraft(
    project_id: str,
    *,
    java_version: int,
    xms: str,
    xmx: str,
    server_jar: str,
    launch_mode: str = "jar",
    game_port: int,
    eula_accepted: bool,
    rcon_enabled: bool = False,
    rcon_port: int = 25575,
    rcon_host_port: int = 25575,
    rcon_password: str | None = None,
    cpu_cores: float | None = None,
    memory_mb: int | None = None,
) -> dict[str, Any]:
    """Start a hardened Minecraft Forge container with validated settings."""
    if not PROJECT_ID_PATTERN.fullmatch(project_id):
        raise ValueError("Invalid project ID")
    if java_version not in {8, 11, 16, 17, 21, 25}:
        raise ValueError("Unsupported Java version")
    minimum = _memory_megabytes(xms)
    maximum = _memory_megabytes(xmx)
    if minimum > maximum or maximum > 65536:
        raise ValueError("Invalid Minecraft memory limits")
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,255}\.jar", server_jar):
        raise ValueError("Invalid server JAR name")
    if launch_mode not in {"jar", "forge_script"}:
        raise ValueError("Invalid Minecraft launch mode")
    if not eula_accepted:
        raise ValueError("Minecraft EULA must be accepted")
    if not 1024 <= game_port <= 65535:
        raise ValueError("Invalid game port")
    if rcon_enabled and (not 1024 <= rcon_port <= 65535 or not 1024 <= rcon_host_port <= 65535):
        raise ValueError("Invalid RCON port")
    root = safe_child(settings.storage_root, f"minecraft/{project_id}")
    root.mkdir(parents=True, exist_ok=True)
    if launch_mode == "jar":
        jar = safe_child(root, server_jar)
        if not jar.is_file() or jar.is_symlink():
            raise ValueError("Minecraft server JAR does not exist")
    else:
        script = safe_child(root, "run.sh")
        if not script.is_file() or script.is_symlink():
            raise ValueError("Forge run.sh launcher does not exist")
        _write_forge_jvm_arguments(root, xms=xms, xmx=xmx)
    (root / "eula.txt").write_text("eula=true\n", encoding="utf-8")
    _write_server_properties(
        root,
        game_port=game_port,
        rcon_enabled=rcon_enabled,
        rcon_port=rcon_port,
        rcon_password=rcon_password,
    )
    container = f"vps-project-{project_id}"
    inspect = await asyncio.create_subprocess_exec(
        str(settings.docker_binary),
        "container",
        "inspect",
        container,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    if await inspect.wait() == 0:
        await run_command([str(settings.docker_binary), "rm", "--force", container])
    arguments = [
        str(settings.docker_binary),
        "run",
        "--detach",
        "--interactive",
        "--name",
        container,
        "--restart",
        "unless-stopped",
        "--security-opt",
        "no-new-privileges:true",
        "--cap-drop",
        "ALL",
        "--label",
        f"io.nectarine.project={project_id}",
        "--publish",
        f"{game_port}:{game_port}/tcp",
        *_docker_resource_arguments(cpu_cores=cpu_cores, memory_mb=memory_mb),
        "--volume",
        f"{root}:/server",
        "--workdir",
        "/server",
    ]
    if rcon_enabled:
        arguments.extend(["--publish", f"127.0.0.1:{rcon_host_port}:{rcon_port}/tcp"])
    arguments.append(f"eclipse-temurin:{java_version}-jre")
    if launch_mode == "forge_script":
        arguments.extend(["sh", "./run.sh", "nogui"])
    else:
        arguments.extend(
            [
                "java",
                f"-Xms{xms.upper()}",
                f"-Xmx{xmx.upper()}",
                "-jar",
                server_jar,
                "nogui",
            ]
        )
    result = await run_command(arguments, command_timeout=180)
    return {"container": container, "container_id": result["stdout"].strip()}


def _rcon_request(port: int, password: str, command: str) -> str:
    """Send one Source RCON command to the local Minecraft server."""

    def packet(request_id: int, packet_type: int, body: str) -> bytes:
        payload = struct.pack("<ii", request_id, packet_type) + body.encode() + b"\x00\x00"
        return struct.pack("<i", len(payload)) + payload

    def receive(connection: socket.socket) -> tuple[int, int, str]:
        length_data = connection.recv(4)
        if len(length_data) != 4:
            raise CommandError("Incomplete RCON response")
        length = struct.unpack("<i", length_data)[0]
        if not 10 <= length <= 4 * 1024 * 1024:
            raise CommandError("Invalid RCON response length")
        payload = bytearray()
        while len(payload) < length:
            chunk = connection.recv(length - len(payload))
            if not chunk:
                raise CommandError("RCON connection closed")
            payload.extend(chunk)
        request_id, packet_type = struct.unpack("<ii", payload[:8])
        return request_id, packet_type, payload[8:-2].decode(errors="replace")

    with socket.create_connection(("127.0.0.1", port), timeout=10) as connection:
        connection.sendall(packet(1, 3, password))
        auth_id, _, _ = receive(connection)
        if auth_id == -1:
            raise CommandError("RCON authentication failed")
        connection.sendall(packet(2, 2, command))
        response_id, _, body = receive(connection)
        if response_id != 2:
            raise CommandError("Unexpected RCON response")
        return body


async def minecraft_command(
    project_id: str,
    *,
    rcon_host_port: int,
    rcon_password: str,
    command: str,
) -> dict[str, Any]:
    """Execute a command through authenticated local-only RCON."""
    if not PROJECT_ID_PATTERN.fullmatch(project_id):
        raise ValueError("Invalid project ID")
    if not 1024 <= rcon_host_port <= 65535:
        raise ValueError("Invalid RCON port")
    if not command.strip() or len(command) > 1024 or "\x00" in command:
        raise ValueError("Invalid Minecraft command")
    output = await asyncio.to_thread(
        _rcon_request,
        rcon_host_port,
        rcon_password,
        command,
    )
    return {"exit_code": 0, "stdout": output, "stderr": ""}


async def _minecraft_container_running(project_id: str) -> bool:
    """Return whether the project's managed Minecraft container is running."""
    try:
        result = await run_command(
            [
                str(settings.docker_binary),
                "container",
                "inspect",
                "--format",
                "{{.State.Running}}",
                f"vps-project-{project_id}",
            ]
        )
    except CommandError:
        return False
    return str(result["stdout"]).strip().lower() == "true"


async def minecraft_backup(
    project_id: str,
    *,
    action: str,
    rcon_host_port: int,
    rcon_password: str | None,
) -> dict[str, Any]:
    """Flush or resume Minecraft world writes around a filesystem backup."""
    if not PROJECT_ID_PATTERN.fullmatch(project_id):
        raise ValueError("Invalid project ID")
    if action not in {"prepare", "resume"}:
        raise ValueError("Invalid Minecraft backup action")
    if not 1024 <= rcon_host_port <= 65535:
        raise ValueError("Invalid RCON port")
    if not await _minecraft_container_running(project_id):
        return {"running": False, "prepared": False}
    if (
        rcon_password is None
        or len(rcon_password) < 16
        or len(rcon_password) > 128
        or any(character in rcon_password for character in "\r\n\x00")
    ):
        raise ValueError("RCON is required to back up a running Minecraft server")
    if action == "resume":
        output = await asyncio.to_thread(
            _rcon_request,
            rcon_host_port,
            rcon_password,
            "save-on",
        )
        return {"running": True, "prepared": False, "output": output}
    await asyncio.to_thread(
        _rcon_request,
        rcon_host_port,
        rcon_password,
        "save-off",
    )
    try:
        output = await asyncio.to_thread(
            _rcon_request,
            rcon_host_port,
            rcon_password,
            "save-all flush",
        )
    except Exception:
        with contextlib.suppress(Exception):
            await asyncio.to_thread(
                _rcon_request,
                rcon_host_port,
                rcon_password,
                "save-on",
            )
        raise
    return {"running": True, "prepared": True, "output": output}


def _sftp_username(project_id: str) -> str:
    """Derive a short deterministic POSIX username from a project UUID."""
    if not PROJECT_ID_PATTERN.fullmatch(project_id):
        raise ValueError("Invalid project ID")
    return f"vps_{project_id.replace('-', '')[:12]}"


async def _sftp_mount_unit_name(target: Path) -> str:
    """Derive and validate the systemd mount unit name for a contained path."""
    if any(character.isspace() or character == "%" for character in str(target)):
        raise ValueError("SFTP mount path contains unsupported characters")
    result = await run_command(
        [
            str(settings.systemd_escape_binary),
            "--path",
            "--suffix=mount",
            str(target),
        ]
    )
    unit_name = str(result["stdout"]).strip()
    if len(unit_name) > 255 or not MOUNT_UNIT_PATTERN.fullmatch(unit_name):
        raise ValueError("Invalid generated mount unit name")
    return unit_name


async def _sftp_mount_unit(project_id: str, source: Path, target: Path) -> tuple[str, Path]:
    """Write the managed bind-mount unit for one project SFTP account."""
    unit_name = await _sftp_mount_unit_name(target)
    unit_path = safe_child(settings.systemd_unit_root, unit_name)
    content = "\n".join(
        [
            f"# Managed by NectarinePanel for project {project_id}",
            "[Unit]",
            f"Description=NectarinePanel SFTP mount for {project_id}",
            "Before=ssh.service",
            "",
            "[Mount]",
            f"What={source}",
            f"Where={target}",
            "Type=none",
            "Options=bind",
            "",
            "[Install]",
            "WantedBy=multi-user.target",
            "",
        ]
    )
    unit_path.write_text(content, encoding="utf-8")
    os.chmod(unit_path, 0o640)
    return unit_name, unit_path


async def enable_sftp(
    project_id: str,
    *,
    password: str | None = None,
    public_key: str | None = None,
) -> dict[str, Any]:
    """Create a chrooted per-project SFTP user with writable uploads only."""
    if not password and not public_key:
        raise ValueError("Password or public key is required")
    if password and (len(password) < 20 or "\x00" in password or "\n" in password):
        raise ValueError("Invalid SFTP password")
    if public_key and (
        len(public_key) > 16384
        or "\n" in public_key.strip()
        or not public_key.startswith(("ssh-ed25519 ", "ssh-rsa "))
    ):
        raise ValueError("Invalid SSH public key")
    username = _sftp_username(project_id)
    project_root = safe_child(settings.storage_root, f"projects/{project_id}")
    uploads = safe_child(project_root, "uploads")
    chroot = safe_child(settings.storage_root, f"sftp/{project_id}")
    mountpoint = safe_child(chroot, "uploads")
    project_root.mkdir(parents=True, exist_ok=True)
    uploads.mkdir(parents=True, exist_ok=True)
    chroot.mkdir(parents=True, exist_ok=True)
    mountpoint.mkdir(parents=True, exist_ok=True)
    os.chown(chroot, 0, 0)
    # OpenSSH requires every ChrootDirectory parent to be root-owned and non-writable.
    os.chmod(chroot, 0o755)  # noqa: S103
    check = await asyncio.create_subprocess_exec(
        "/usr/bin/id",
        username,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    if await check.wait() != 0:
        await run_command(
            [
                str(settings.useradd_binary),
                "--home-dir",
                "/uploads",
                "--shell",
                "/usr/sbin/nologin",
                "--no-create-home",
                username,
            ]
        )
    account = pwd.getpwnam(username)
    os.chown(uploads, account.pw_uid, account.pw_gid)
    # The account owner needs write access while other local users stay excluded.
    os.chmod(uploads, 0o750)  # noqa: S103
    mount_unit, _ = await _sftp_mount_unit(project_id, uploads, mountpoint)
    await run_command([str(settings.systemctl_binary), "daemon-reload"])
    await run_command(
        [str(settings.systemctl_binary), "enable", "--now", mount_unit],
        command_timeout=120,
    )
    if password:
        await run_command(
            [str(settings.chpasswd_binary)],
            stdin_data=f"{username}:{password}\n".encode(),
        )
    if public_key:
        ssh_directory = uploads / ".ssh"
        ssh_directory.mkdir(mode=0o700, exist_ok=True)
        authorized_keys = ssh_directory / "authorized_keys"
        authorized_keys.write_text(public_key.strip() + "\n", encoding="utf-8")
        os.chown(ssh_directory, account.pw_uid, account.pw_gid)
        os.chown(authorized_keys, account.pw_uid, account.pw_gid)
        os.chmod(authorized_keys, 0o600)
    settings.sshd_config_root.mkdir(parents=True, exist_ok=True)
    config = settings.sshd_config_root / f"90-vps-panel-{project_id}.conf"
    config.write_text(
        "\n".join(
            [
                f"Match User {username}",
                f"    ChrootDirectory {chroot}",
                "    ForceCommand internal-sftp -d /uploads",
                "    AllowTcpForwarding no",
                "    X11Forwarding no",
                "    PermitTunnel no",
                "    GatewayPorts no",
                "",
            ]
        ),
        encoding="utf-8",
    )
    os.chmod(config, 0o640)
    await run_command([str(settings.sshd_binary), "-t"])
    await run_command([str(settings.systemctl_binary), "reload", "ssh.service"])
    return {
        "username": username,
        "directory": "/uploads",
        "host_directory": str(uploads),
    }


async def disable_sftp(project_id: str) -> dict[str, Any]:
    """Disable a project SFTP account without deleting uploaded files."""
    username = _sftp_username(project_id)
    chroot = safe_child(settings.storage_root, f"sftp/{project_id}")
    mountpoint = safe_child(chroot, "uploads")
    mount_unit = await _sftp_mount_unit_name(mountpoint)
    mount_unit_path = safe_child(settings.systemd_unit_root, mount_unit)
    _assert_managed_unit(mount_unit_path, project_id)
    config = safe_child(settings.sshd_config_root, f"90-vps-panel-{project_id}.conf")
    changed = config.is_file()
    config.unlink(missing_ok=True)
    if mount_unit_path.exists():
        await run_command(
            [str(settings.systemctl_binary), "disable", "--now", mount_unit],
            command_timeout=120,
        )
        mount_unit_path.unlink()
        await run_command([str(settings.systemctl_binary), "daemon-reload"])
        changed = True
    check = await asyncio.create_subprocess_exec(
        "/usr/bin/id",
        username,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    if await check.wait() == 0:
        await run_command([str(settings.userdel_binary), username])
        changed = True
    if changed:
        await run_command([str(settings.sshd_binary), "-t"])
        await run_command([str(settings.systemctl_binary), "reload", "ssh.service"])
    return {"username": username, "disabled": True, "changed": changed}


async def cleanup_project(
    project_id: str,
    runtime_type: str,
    hostnames: list[str],
) -> dict[str, Any]:
    """Remove all host resources owned by one project using fixed identifiers."""
    if not PROJECT_ID_PATTERN.fullmatch(project_id):
        raise ValueError("Invalid project ID")
    if runtime_type not in RUNTIME_TYPES:
        raise ValueError("Unsupported project runtime")
    if not isinstance(hostnames, list) or len(hostnames) > 100:
        raise ValueError("Invalid project hostnames")
    normalized_hostnames: list[str] = []
    for hostname in hostnames:
        if not isinstance(hostname, str):
            raise ValueError("Invalid project hostname")
        _validate_hostname(hostname)
        if hostname not in normalized_hostnames:
            normalized_hostnames.append(hostname)

    if runtime_type in {"systemd", "pm2"}:
        _, unit_path = _project_service_path(project_id)
        _assert_managed_unit(unit_path, project_id)
    chroot = safe_child(settings.storage_root, f"sftp/{project_id}")
    mount_unit = await _sftp_mount_unit_name(safe_child(chroot, "uploads"))
    mount_unit_path = safe_child(settings.systemd_unit_root, mount_unit)
    _assert_managed_unit(mount_unit_path, project_id)

    runtime_removed: dict[str, Any] = {"type": runtime_type, "removed": False}
    if runtime_type in {"systemd", "pm2"}:
        runtime_removed["removed"] = await _remove_project_service(project_id)
        runtime_removed["user_removed"] = await _remove_project_runtime_user(project_id)
    elif runtime_type in {"docker", "minecraft_forge"}:
        runtime_removed["removed"] = await _remove_project_container(project_id)
        runtime_removed["images"] = await _remove_project_images(project_id)
    elif runtime_type == "docker_compose":
        runtime_removed.update(await _remove_compose_project(project_id))

    sftp = await disable_sftp(project_id)
    nginx_changed = False
    for hostname in normalized_hostnames:
        result = remove_nginx_config(hostname)
        nginx_changed = nginx_changed or bool(result["changed"])
    if nginx_changed:
        await validate_nginx()
        await reload_nginx()
    certificates = [await remove_certificate(hostname) for hostname in normalized_hostnames]

    removed_directories: list[str] = []
    for relative_path in (
        f"projects/{project_id}",
        f"minecraft/{project_id}",
        f"sftp/{project_id}",
    ):
        namespace, _, identifier = relative_path.partition("/")
        if namespace not in {"projects", "minecraft", "sftp"} or identifier != project_id:
            raise ValueError("Invalid project storage directory")
        path = settings.storage_root.resolve() / namespace / identifier
        if path.is_symlink():
            path.unlink()
            removed_directories.append(relative_path)
        elif path.exists():
            shutil.rmtree(path)
            removed_directories.append(relative_path)
    return {
        "project_id": project_id,
        "runtime": runtime_removed,
        "sftp": sftp,
        "nginx_reloaded": nginx_changed,
        "certificates": certificates,
        "removed_directories": removed_directories,
    }


def host_metrics() -> dict[str, Any]:
    """Collect current host resource metrics."""
    memory = psutil.virtual_memory()
    disk = psutil.disk_usage("/")
    network = psutil.net_io_counters()
    return {
        "cpu_percent": psutil.cpu_percent(interval=None),
        "memory_percent": memory.percent,
        "memory_used": memory.used,
        "memory_total": memory.total,
        "disk_percent": disk.percent,
        "disk_used": disk.used,
        "disk_total": disk.total,
        "network_bytes_sent": network.bytes_sent,
        "network_bytes_received": network.bytes_recv,
        "boot_time": psutil.boot_time(),
    }


def _parse_docker_bytes(value: str) -> int:
    """Parse Docker stats byte values into bytes."""
    match = re.fullmatch(r"\s*([0-9]+(?:[.][0-9]+)?)\s*([A-Za-z]+)\s*", value)
    if match is None:
        return 0
    amount = float(match.group(1))
    unit = match.group(2).lower()
    multipliers = {
        "b": 1,
        "kb": 1000,
        "kib": 1024,
        "mb": 1000**2,
        "mib": 1024**2,
        "gb": 1000**3,
        "gib": 1024**3,
        "tb": 1000**4,
        "tib": 1024**4,
    }
    return int(amount * multipliers.get(unit, 0))


def _parse_percent(value: object) -> float:
    """Parse a bounded percent value from Docker or psutil output."""
    try:
        percent = float(str(value).strip().rstrip("%"))
    except ValueError:
        return 0.0
    return max(0.0, min(percent, 10_000.0))


def _docker_stats_values(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Normalize one or more Docker stats rows."""
    cpu_percent = 0.0
    memory_used = 0
    memory_total = 0
    network_sent = 0
    network_received = 0
    for row in rows:
        cpu_percent += _parse_percent(row.get("CPUPerc", 0))
        memory_usage = str(row.get("MemUsage", ""))
        used, _, total = memory_usage.partition("/")
        memory_used += _parse_docker_bytes(used)
        memory_total += _parse_docker_bytes(total)
        network = str(row.get("NetIO", ""))
        received, _, sent = network.partition("/")
        network_received += _parse_docker_bytes(received)
        network_sent += _parse_docker_bytes(sent)
    memory_percent = (memory_used / memory_total * 100) if memory_total else 0.0
    return {
        "resource_status": "running" if rows else "stopped",
        "cpu_percent": round(cpu_percent, 2),
        "memory_percent": round(memory_percent, 2),
        "memory_used": memory_used,
        "memory_total": memory_total,
        "network_bytes_sent": network_sent,
        "network_bytes_received": network_received,
        "process_count": len(rows),
    }


async def _docker_container_stats(containers: list[str]) -> dict[str, Any]:
    """Collect Docker stats for fixed, prevalidated container names or IDs."""
    if not containers:
        return _docker_stats_values([])
    if any(not DOCKER_RESOURCE_PATTERN.fullmatch(container) for container in containers):
        raise ValueError("Invalid Docker container identifier")
    try:
        result = await run_command(
            [
                str(settings.docker_binary),
                "stats",
                "--no-stream",
                "--format",
                "{{json .}}",
                *containers,
            ],
            command_timeout=60,
            output_limit=1024 * 1024,
        )
    except CommandError:
        return _docker_stats_values([])
    rows: list[dict[str, Any]] = []
    for line in str(result["stdout"]).splitlines():
        if not line.strip():
            continue
        parsed = json.loads(line)
        if not isinstance(parsed, dict):
            raise CommandError("Docker stats returned invalid JSON")
        rows.append(parsed)
    return _docker_stats_values(rows)


async def _compose_project_containers(project_id: str) -> list[str]:
    """Return containers owned by one Docker Compose project label."""
    result = await run_command(
        [
            str(settings.docker_binary),
            "container",
            "ls",
            "--quiet",
            "--filter",
            f"label=com.docker.compose.project=vps-project-{project_id}",
        ],
        command_timeout=60,
    )
    return _validated_docker_resources(str(result["stdout"]))


async def _process_tree_metrics(pid: int) -> dict[str, Any]:
    """Collect process-tree memory and CPU metrics for a systemd service."""
    if pid <= 0:
        return {
            "resource_status": "stopped",
            "cpu_percent": 0.0,
            "memory_percent": 0.0,
            "memory_used": 0,
            "memory_total": psutil.virtual_memory().total,
            "network_bytes_sent": 0,
            "network_bytes_received": 0,
            "process_count": 0,
        }
    try:
        root = psutil.Process(pid)
        processes = [root, *root.children(recursive=True)]
    except psutil.Error:
        processes = []
    for process in processes:
        with contextlib.suppress(psutil.Error):
            process.cpu_percent(interval=None)
    if processes:
        await asyncio.sleep(0.1)
    memory_total = psutil.virtual_memory().total
    memory_used = 0
    cpu_percent = 0.0
    alive = 0
    for process in processes:
        try:
            memory_used += process.memory_info().rss
            cpu_percent += process.cpu_percent(interval=None)
            alive += 1
        except psutil.Error:
            continue
    return {
        "resource_status": "running" if alive else "stopped",
        "cpu_percent": round(cpu_percent, 2),
        "memory_percent": round(memory_used / memory_total * 100, 2) if memory_total else 0.0,
        "memory_used": memory_used,
        "memory_total": memory_total,
        "network_bytes_sent": 0,
        "network_bytes_received": 0,
        "process_count": alive,
    }


def _bounded_directory_size(root: Path, *, max_entries: int = 100_000) -> int:
    """Calculate directory size without following symlinks or scanning unboundedly."""
    if not root.is_dir():
        raise ValueError("Project root does not exist")
    total = 0
    entries = 0
    pending = [root]
    while pending and entries < max_entries:
        directory = pending.pop()
        try:
            children = list(directory.iterdir())
        except OSError:
            continue
        for child in children:
            entries += 1
            if entries > max_entries:
                break
            try:
                if child.is_symlink():
                    continue
                if child.is_dir():
                    pending.append(child)
                elif child.is_file():
                    total += child.stat().st_size
            except OSError:
                continue
    return total


async def _systemd_project_metrics(
    project_id: str,
    service: str | None = None,
) -> dict[str, Any]:
    """Collect metrics for one validated managed or imported systemd unit."""
    if service is None:
        service, _ = _project_service_path(project_id)
    elif not SERVICE_PATTERN.fullmatch(service):
        raise ValueError("Invalid project service name")
    try:
        result = await run_command(
            [
                str(settings.systemctl_binary),
                "show",
                service,
                "--property=MainPID",
                "--value",
            ],
            command_timeout=30,
        )
    except CommandError:
        return await _process_tree_metrics(0)
    try:
        pid = int(str(result["stdout"]).strip() or "0")
    except ValueError:
        pid = 0
    return await _process_tree_metrics(pid)


async def project_metrics(
    project_id: str,
    runtime_type: str,
    service: str | None = None,
    project_root: str | None = None,
) -> dict[str, Any]:
    """Collect current resource metrics for one project runtime."""
    if not PROJECT_ID_PATTERN.fullmatch(project_id):
        raise ValueError("Invalid project ID")
    if runtime_type not in RUNTIME_TYPES:
        raise ValueError("Unsupported project runtime")
    if runtime_type in {"docker", "minecraft_forge"}:
        metrics = await _docker_container_stats([f"vps-project-{project_id}"])
    elif runtime_type == "docker_compose":
        metrics = await _docker_container_stats(await _compose_project_containers(project_id))
    elif runtime_type in {"systemd", "pm2"}:
        metrics = await _systemd_project_metrics(project_id, service)
    else:
        metrics = {
            "resource_status": "not_applicable",
            "cpu_percent": 0.0,
            "memory_percent": 0.0,
            "memory_used": 0,
            "memory_total": psutil.virtual_memory().total,
            "network_bytes_sent": 0,
            "network_bytes_received": 0,
            "process_count": 0,
        }
    result = {
        "project_id": project_id,
        "runtime_type": runtime_type,
        **metrics,
    }
    if project_root is not None:
        result["disk_bytes"] = _bounded_directory_size(_external_project_root(project_root))
    return result
