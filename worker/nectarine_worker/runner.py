"""Safe subprocess and archive helpers for background jobs."""

import base64
import hashlib
import json
import os
import shutil
import subprocess
import tarfile
import tempfile
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.version import APP_VERSION
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

ALLOWED_EXECUTABLES = frozenset({"git", "mysql", "mysqldump", "pg_dump", "pg_restore"})
BACKUP_MAGIC = b"NPB1"
BACKUP_FORMAT_VERSION = 1
RESTORE_COMPATIBILITY = ">=0.1.0,<1.0.0"
LOGICAL_BACKUP_PATHS = {".env": "shared/runtime.env"}
MAX_ARCHIVE_MEMBERS = 100_000
MAX_ARCHIVE_EXPANDED_BYTES = 20 * 1024 * 1024 * 1024


class RunnerError(RuntimeError):
    """Raised when a worker subprocess fails."""


def run(
    arguments: list[str],
    cwd: Path,
    timeout: int = 1800,
    *,
    env_overrides: dict[str, str] | None = None,
    stdin_path: Path | None = None,
) -> str:
    """Run a command without a shell and return bounded combined output."""
    if not arguments or Path(arguments[0]).name not in ALLOWED_EXECUTABLES:
        raise RunnerError("Executable is not allowlisted")
    environment = {
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        "LANG": "C.UTF-8",
    }
    environment.update(env_overrides or {})
    stdin = stdin_path.open("rb") if stdin_path else None
    try:
        completed = subprocess.run(  # noqa: S603 - argv executable is allowlisted above
            arguments,
            cwd=cwd,
            env=environment,
            stdin=stdin,
            capture_output=True,
            text=False,
            timeout=timeout,
            check=False,
        )
    finally:
        if stdin is not None:
            stdin.close()
    output = (
        completed.stdout.decode("utf-8", errors="replace")
        + "\n"
        + completed.stderr.decode("utf-8", errors="replace")
    )[-262144:]
    if completed.returncode != 0:
        raise RunnerError(output)
    return output


def run_project_command(
    command: str,
    cwd: Path,
    *,
    timeout: int = 1800,
    env_overrides: dict[str, str] | None = None,
) -> str:
    """Run a project-defined build command inside its release directory."""
    normalized = command.strip()
    if not normalized or len(normalized) > 4000:
        raise RunnerError("Project command is empty or too long")
    if "\x00" in normalized or "\n" in normalized or "\r" in normalized:
        raise RunnerError("Project command contains unsupported control characters")
    environment = {
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        "LANG": "C.UTF-8",
        "HOME": str(cwd),
    }
    environment.update(env_overrides or {})
    completed = subprocess.run(  # noqa: S602 - owner-configured project build command.
        normalized,
        cwd=cwd,
        env=environment,
        capture_output=True,
        text=False,
        timeout=timeout,
        check=False,
        shell=True,
        executable="/bin/sh",
    )
    output = (
        completed.stdout.decode("utf-8", errors="replace")
        + "\n"
        + completed.stderr.decode("utf-8", errors="replace")
    )[-262144:]
    if completed.returncode != 0:
        raise RunnerError(output)
    return output


def extract_zip_safely(archive: Path, destination: Path) -> None:
    """Extract a ZIP archive after path and symlink validation."""
    destination = destination.resolve()
    destination.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as source:
        members = source.infolist()
        if len(members) > MAX_ARCHIVE_MEMBERS:
            raise RunnerError("Archive contains too many entries")
        if sum(member.file_size for member in members) > MAX_ARCHIVE_EXPANDED_BYTES:
            raise RunnerError("Archive expanded size exceeds the safety limit")
        for member in members:
            target = (destination / member.filename).resolve()
            if destination != target and destination not in target.parents:
                raise RunnerError(f"Unsafe archive path: {member.filename}")
            mode = member.external_attr >> 16
            if mode and (mode & 0o170000) == 0o120000:
                raise RunnerError("Archive symlinks are not allowed")
            if member.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            descriptor = os.open(target, flags, 0o640)
            with source.open(member) as source_file, os.fdopen(descriptor, "wb") as target_file:
                shutil.copyfileobj(source_file, target_file)


def extract_tar_safely(archive: Path, destination: Path) -> None:
    """Extract a TAR archive without traversal, links, or device files."""
    destination = destination.resolve()
    destination.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, "r:*") as source:
        members = source.getmembers()
        if len(members) > MAX_ARCHIVE_MEMBERS:
            raise RunnerError("Archive contains too many entries")
        if sum(member.size for member in members) > MAX_ARCHIVE_EXPANDED_BYTES:
            raise RunnerError("Archive expanded size exceeds the safety limit")
        for member in members:
            target = (destination / member.name).resolve()
            if destination != target and destination not in target.parents:
                raise RunnerError(f"Unsafe archive path: {member.name}")
            if member.issym() or member.islnk() or member.isdev():
                raise RunnerError("Archive links and device files are not allowed")
        for member in members:
            source.extract(member, destination, filter="data")


def create_tar_gz_safely(source: Path, destination: Path, root: Path) -> None:
    """Create a TAR.GZ archive without links or special filesystem entries."""
    root = root.resolve(strict=True)
    source = source.resolve(strict=True)
    destination = destination.resolve()
    if source != root and root not in source.parents:
        raise RunnerError("Archive source escapes project root")
    if source.is_dir() and (destination == source or source in destination.parents):
        raise RunnerError("Archive destination cannot be inside its source")
    with tarfile.open(destination, "x:gz", format=tarfile.PAX_FORMAT) as archive:
        archive.add(
            source,
            arcname=source.relative_to(root),
            recursive=True,
            filter=_archive_filter,
        )


def sha256_file(path: Path) -> str:
    """Calculate a streaming SHA-256 checksum."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _decode_backup_key(value: str) -> bytes:
    """Decode and validate a URL-safe 256-bit backup encryption key."""
    try:
        key = base64.urlsafe_b64decode(value.encode("ascii"))
    except (ValueError, UnicodeEncodeError) as exc:
        raise RunnerError("Invalid backup encryption key") from exc
    if len(key) != 32:
        raise RunnerError("Backup encryption key must contain 256 bits")
    return key


def encrypt_backup(source: Path, destination: Path, key_value: str) -> None:
    """Encrypt a backup using streaming AES-256-GCM."""
    key = _decode_backup_key(key_value)
    nonce = os.urandom(12)
    encryptor = Cipher(algorithms.AES(key), modes.GCM(nonce)).encryptor()
    with source.open("rb") as source_file, destination.open("xb") as target_file:
        target_file.write(BACKUP_MAGIC)
        target_file.write(nonce)
        for chunk in iter(lambda: source_file.read(1024 * 1024), b""):
            target_file.write(encryptor.update(chunk))
        target_file.write(encryptor.finalize())
        target_file.write(encryptor.tag)


def decrypt_backup(source: Path, destination: Path, key_value: str) -> None:
    """Decrypt and authenticate a streaming NectarinePanel backup."""
    key = _decode_backup_key(key_value)
    size = source.stat().st_size
    if size < len(BACKUP_MAGIC) + 12 + 16:
        raise RunnerError("Encrypted backup is truncated")
    with source.open("rb") as source_file:
        if source_file.read(len(BACKUP_MAGIC)) != BACKUP_MAGIC:
            raise RunnerError("Encrypted backup header is invalid")
        nonce = source_file.read(12)
        source_file.seek(-16, os.SEEK_END)
        tag = source_file.read(16)
        source_file.seek(len(BACKUP_MAGIC) + 12)
        remaining = size - len(BACKUP_MAGIC) - 12 - 16
        decryptor = Cipher(algorithms.AES(key), modes.GCM(nonce, tag)).decryptor()
        with destination.open("xb") as target_file:
            while remaining:
                chunk = source_file.read(min(1024 * 1024, remaining))
                if not chunk:
                    raise RunnerError("Encrypted backup is truncated")
                remaining -= len(chunk)
                target_file.write(decryptor.update(chunk))
            try:
                target_file.write(decryptor.finalize())
            except ValueError as exc:
                destination.unlink(missing_ok=True)
                raise RunnerError("Encrypted backup authentication failed") from exc


def _archive_filter(member: tarfile.TarInfo) -> tarfile.TarInfo | None:
    """Exclude links and special files from newly created backups."""
    if member.issym() or member.islnk() or member.isdev() or member.isfifo():
        return None
    member.mode &= 0o777
    return member


def create_backup(
    source: Path,
    target: Path,
    *,
    backup_type: str,
    project_id: str | None,
    database_id: str | None = None,
    include_items: list[str] | None = None,
    encryption_key: str | None = None,
) -> dict[str, Any]:
    """Create a compressed backup and adjacent manifest."""
    source = source.resolve(strict=True)
    target.parent.mkdir(parents=True, exist_ok=True)
    requested_items = include_items or ["all"]
    if not requested_items or ("all" in requested_items and len(requested_items) != 1):
        raise RunnerError("Backup items must be either all or an explicit list")
    paths = list(source.iterdir()) if requested_items == ["all"] else []
    if requested_items != ["all"]:
        for item in requested_items:
            if item.startswith("/") or item in {"", ".", ".."}:
                raise RunnerError("Invalid backup item")
            stored_path = LOGICAL_BACKUP_PATHS.get(item, item)
            candidate = (source / stored_path).resolve()
            if source not in candidate.parents or not candidate.exists():
                raise RunnerError(f"Backup item is unavailable: {item}")
            paths.append(source / stored_path)
    with tarfile.open(target, "w:gz", format=tarfile.PAX_FORMAT) as archive:
        root_info = tarfile.TarInfo(source.name)
        root_info.type = tarfile.DIRTYPE
        root_info.mode = 0o750
        root_info.mtime = int(source.stat().st_mtime)
        archive.addfile(root_info)
        for path in paths:
            resolved = path.resolve()
            if source not in resolved.parents:
                raise RunnerError("Backup item escapes source root")
            archive.add(
                resolved,
                arcname=f"{source.name}/{path.relative_to(source)}",
                recursive=True,
                filter=_archive_filter,
            )
    artifact = target
    encrypted = encryption_key is not None
    if encryption_key:
        artifact = target.with_suffix(target.suffix + ".enc")
        encrypt_backup(target, artifact, encryption_key)
        target.unlink()
    manifest = {
        "format_version": BACKUP_FORMAT_VERSION,
        "type": backup_type,
        "created_at": datetime.now(UTC).isoformat(),
        "project_id": project_id,
        "database_id": database_id,
        "included_items": requested_items,
        "size_bytes": artifact.stat().st_size,
        "checksum": sha256_file(artifact),
        "encrypted": encrypted,
        "encryption": "aes-256-gcm" if encrypted else None,
        "app_version": APP_VERSION,
        "restore_compatibility": RESTORE_COMPATIBILITY,
        "artifact_path": str(artifact),
    }
    manifest_path = artifact.with_suffix(artifact.suffix + ".manifest.json")
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def restore_backup(
    archive: Path,
    destination: Path,
    manifest_path: Path,
    *,
    encryption_key: str | None = None,
    expected_project_id: str | None = None,
) -> None:
    """Verify and atomically restore a backup archive."""
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("format_version", 1) != BACKUP_FORMAT_VERSION:
        raise RunnerError("Unsupported backup format version")
    if manifest.get("type") != "project":
        raise RunnerError("Backup type is not restorable as a project")
    if manifest.get("restore_compatibility") != RESTORE_COMPATIBILITY:
        raise RunnerError("Backup is not compatible with this panel version")
    if expected_project_id is not None and manifest.get("project_id") != expected_project_id:
        raise RunnerError("Backup belongs to another project")
    if manifest.get("checksum") != sha256_file(archive):
        raise RunnerError("Backup checksum mismatch")
    destination = destination.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".restore-", dir=destination.parent) as temporary:
        staging = Path(temporary)
        source_archive = archive
        if manifest.get("encrypted"):
            if not encryption_key:
                raise RunnerError("Backup encryption key is required")
            source_archive = staging / "decrypted.tar.gz"
            decrypt_backup(archive, source_archive, encryption_key)
        extract_tar_safely(source_archive, staging / "content")
        content = staging / "content"
        roots = list(content.iterdir())
        if len(roots) != 1 or not roots[0].is_dir():
            raise RunnerError("Backup must contain exactly one root directory")
        replacement = destination.parent / f".{destination.name}.replacement"
        previous = destination.parent / f".{destination.name}.previous"
        shutil.rmtree(replacement, ignore_errors=True)
        shutil.rmtree(previous, ignore_errors=True)
        roots[0].replace(replacement)
        if destination.exists():
            destination.replace(previous)
        try:
            replacement.replace(destination)
        except Exception:
            if previous.exists() and not destination.exists():
                previous.replace(destination)
            raise
        shutil.rmtree(previous, ignore_errors=True)


def validate_backup_archive(archive: Path, *, encryption_key: str | None = None) -> bool:
    """Fully validate an uploaded backup and return whether it is encrypted."""
    with archive.open("rb") as handle:
        encrypted = handle.read(len(BACKUP_MAGIC)) == BACKUP_MAGIC
    with tempfile.TemporaryDirectory(
        prefix=".backup-validation-",
        dir=archive.parent,
    ) as temporary:
        temporary_root = Path(temporary)
        source_archive = archive
        if encrypted:
            if not encryption_key:
                raise RunnerError("Backup encryption key is required")
            source_archive = temporary_root / "decrypted.tar.gz"
            decrypt_backup(archive, source_archive, encryption_key)
        extraction_root = temporary_root / "content"
        extract_tar_safely(source_archive, extraction_root)
        roots = list(extraction_root.iterdir())
        if len(roots) != 1 or not roots[0].is_dir():
            raise RunnerError("Backup must contain exactly one root directory")
    return encrypted
