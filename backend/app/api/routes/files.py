"""Safe unprivileged project file manager endpoints."""

import asyncio
import os
import shutil
import stat
import uuid
from datetime import UTC, datetime
from pathlib import Path

import anyio
import httpx
from fastapi import (
    APIRouter,
    HTTPException,
    Query,
    UploadFile,
    status,
)
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

from app.api.dependencies import AppSettings, CurrentUser, DbSession
from app.models.entities import BUKKIT_TYPES, FileOperation, Job, Project
from app.schemas.common import MessageResponse
from app.schemas.files import (
    ArchiveRequest,
    FileCreateRequest,
    FileDeleteRequest,
    FileEntry,
    FileJobResponse,
    FileMoveRequest,
    FileWriteRequest,
)
from app.services.agent import AgentClient
from app.services.audit import write_audit_log
from app.services.minecraft_operations import assert_minecraft_idle, reserve_minecraft_job
from app.services.paths import UnsafePathError, resolve_within, runtime_root
from app.services.permissions import require_project_permission
from app.services.queue import enqueue

router = APIRouter(prefix="/projects/{project_id}/files", tags=["files"])


async def _root_or_404(project_id: str, session: DbSession, settings: AppSettings) -> Path:
    """Return an existing project root after project validation."""
    project = await session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    try:
        root = runtime_root(settings.storage_root, project)
    except UnsafePathError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if project.runtime_config.get("source_path"):
        try:
            exists = root.is_dir()
        except PermissionError:
            exists = True
        if not exists:
            raise HTTPException(status_code=404, detail="Project source path not found")
    else:
        root.mkdir(parents=True, exist_ok=True)
    return root


async def _agent_browse_files(
    root: Path,
    path: str,
    include_hidden: bool,
    settings: AppSettings,
) -> list[FileEntry]:
    """List a directory through the privileged agent when DAC blocks the backend."""
    result = await AgentClient(settings).execute(
        "list_directory",
        {
            "root": str(root),
            "path": path,
            "include_hidden": include_hidden,
        },
        request_timeout=30,
    )
    entries = result.get("entries", [])
    if not isinstance(entries, list):
        raise HTTPException(status_code=502, detail="Invalid file listing response")
    return [FileEntry.model_validate(entry) for entry in entries]


async def _delete_local_path(target: Path) -> None:
    """Delete one local entry without following symbolic links."""
    if target.is_symlink() or target.is_file():
        target.unlink(missing_ok=False)
    elif target.is_dir():
        await asyncio.to_thread(shutil.rmtree, target)
    else:
        raise HTTPException(status_code=404, detail="Path not found")


def _move_local_path(source: Path, destination: Path) -> None:
    """Move one local entry after existence and collision checks."""
    if not source.exists() or source.is_symlink():
        raise HTTPException(status_code=404, detail="Source path not found")
    if destination.exists() or destination.is_symlink():
        raise HTTPException(status_code=409, detail="Destination already exists")
    destination.parent.mkdir(parents=True, exist_ok=True)
    source.replace(destination)


def _safe_path(root: Path, relative: str) -> Path:
    """Convert containment errors into stable API validation responses."""
    try:
        return resolve_within(root, relative)
    except UnsafePathError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _guard_minecraft_file(project: Project, root: Path, target: Path) -> None:
    """Keep managed launchers and RCON properties behind the Minecraft API."""
    if project.project_type not in BUKKIT_TYPES:
        return
    protected = {root / "server.properties"}
    if project.runtime_config.get("sha256"):
        protected.add(root / "server.jar")
    if any(target == path or target in path.parents for path in protected):
        raise HTTPException(
            status_code=409, detail="Use Minecraft settings or update for managed files"
        )


def _safe_delete_path(root: Path, relative: str) -> Path:
    """Resolve an entry parent while preserving the final symbolic link."""
    relative_path = Path(relative)
    if (
        not relative
        or relative_path.is_absolute()
        or relative_path.name in {"", ".", ".."}
        or any(part == ".." for part in relative_path.parts)
        or any(ord(character) < 32 for character in relative)
    ):
        raise HTTPException(status_code=422, detail="Invalid project path")
    root_resolved = root.resolve()
    parent = (root_resolved / relative_path.parent).resolve()
    if parent != root_resolved and root_resolved not in parent.parents:
        raise HTTPException(status_code=422, detail="Path escapes the allowed root")
    return parent / relative_path.name


def _entry(root: Path, path: Path) -> FileEntry:
    """Build metadata without following external symbolic links."""
    metadata = path.lstat()
    relative = path.relative_to(root).as_posix()
    target_kind: str | None = None
    target: Path | None = None
    if path.is_symlink():
        kind = "symlink"
        try:
            resolved_root = root.resolve()
            resolved_target = path.resolve(strict=True)
            if resolved_target == resolved_root or resolved_root in resolved_target.parents:
                target = resolved_target
                if resolved_target.is_dir():
                    target_kind = "directory"
                elif resolved_target.is_file():
                    target_kind = "file"
        except OSError:
            pass
    elif path.is_dir():
        kind = "directory"
        target = path
    else:
        kind = "file"
        target = path
    return FileEntry(
        name=path.name,
        path=relative,
        kind=kind,
        target_kind=target_kind,
        size_bytes=(
            target.stat().st_size
            if target is not None and (kind == "file" or target_kind == "file")
            else _bounded_directory_size(target)
            if target is not None and (kind == "directory" or target_kind == "directory")
            else 0
        ),
        modified_at=datetime.fromtimestamp(metadata.st_mtime, tz=UTC),
        permissions=stat.filemode(metadata.st_mode),
    )


async def _stage_upload(file: UploadFile, settings: AppSettings) -> tuple[Path, int]:
    """Stream an upload into private panel storage before agent publication."""
    staging_root = settings.storage_root / ".uploads"
    staging_root.mkdir(parents=True, exist_ok=True)
    staged_path = staging_root / uuid.uuid4().hex
    total = 0
    try:
        async with await anyio.open_file(staged_path, "xb") as handle:
            while chunk := await file.read(1024 * 1024):
                total += len(chunk)
                if total > settings.max_upload_bytes:
                    raise HTTPException(status_code=413, detail="Upload exceeds size limit")
                await handle.write(chunk)
        os.chmod(staged_path, 0o640)
        return staged_path, total
    except Exception:
        staged_path.unlink(missing_ok=True)
        raise


async def _publish_upload_with_agent(
    root: Path,
    path: str,
    file: UploadFile,
    settings: AppSettings,
) -> tuple[FileEntry, int]:
    """Publish an upload to a protected imported-project root through the agent."""
    staged_path, total = await _stage_upload(file, settings)
    try:
        result = await AgentClient(settings).execute(
            "publish_staged_upload",
            {"root": str(root), "path": path, "staged_path": str(staged_path)},
            request_timeout=300,
        )
    except httpx.HTTPStatusError as exc:
        status_code = 409 if exc.response.status_code == 422 else 502
        detail = (
            "Destination already exists"
            if status_code == 409
            else "Unable to upload project file"
        )
        raise HTTPException(status_code=status_code, detail=detail) from exc
    finally:
        staged_path.unlink(missing_ok=True)
    modified_at = result.get("modified_at")
    if not isinstance(modified_at, str):
        raise HTTPException(status_code=502, detail="Invalid agent upload response")
    return (
        FileEntry(
            name=Path(path).name,
            path=path,
            kind="file",
            target_kind=None,
            size_bytes=int(result.get("size_bytes", total)),
            modified_at=datetime.fromisoformat(modified_at),
            permissions="-rw-r-----",
        ),
        total,
    )


def _bounded_directory_size(path: Path, *, max_entries: int = 100_000) -> int:
    """Calculate a directory size without following symlinks or unbounded scans."""
    total = 0
    entries = 0
    for child in path.rglob("*"):
        entries += 1
        if entries > max_entries:
            break
        try:
            if not child.is_symlink() and child.is_file():
                total += child.stat().st_size
        except OSError:
            continue
    return total


def _local_directory_entries(
    root: Path,
    directory: Path,
    include_hidden: bool,
) -> list[FileEntry]:
    """Build one local directory listing outside the event loop."""
    return [
        _entry(root, item)
        for item in directory.iterdir()
        if include_hidden or not item.name.startswith(".")
    ]


def _is_supported_archive(path: Path) -> bool:
    """Return whether a path uses a supported archive extension."""
    name = path.name.lower()
    return name.endswith(".zip") or name.endswith(".tar.gz")


def _local_download_response(target: Path) -> FileResponse:
    """Return a local file response after rejecting links and non-files."""
    if not target.is_file() or target.is_symlink():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(target, filename=target.name)


def _validate_local_archive_paths(source: Path, destination: Path) -> None:
    """Validate archive paths when the backend can inspect their metadata."""
    if not source.exists() or source.is_symlink():
        raise HTTPException(status_code=404, detail="Archive source not found")
    if destination.exists() or destination.is_symlink():
        raise HTTPException(status_code=409, detail="Archive destination already exists")
    if source.is_dir() and (destination == source or source in destination.parents):
        raise HTTPException(
            status_code=422,
            detail="Archive destination cannot be inside its source",
        )


def _staged_download_response(
    staged_path: str,
    download_id: str,
    filename: str,
    settings: AppSettings,
) -> FileResponse:
    """Validate and stream an agent-staged file with guaranteed cleanup."""
    staging_root = (settings.storage_root / ".downloads").resolve()
    candidate = Path(staged_path).resolve()
    if candidate.parent != staging_root or candidate.name != download_id:
        raise HTTPException(status_code=502, detail="Invalid agent download path")
    try:
        if not candidate.is_file() or candidate.is_symlink():
            raise HTTPException(status_code=502, detail="Agent download is unavailable")
    except PermissionError as exc:
        raise HTTPException(status_code=502, detail="Agent download is unavailable") from exc
    return FileResponse(
        candidate,
        filename=filename,
        background=BackgroundTask(candidate.unlink, missing_ok=True),
    )


@router.get("", response_model=list[FileEntry])
async def browse_files(
    project_id: str,
    user: CurrentUser,
    session: DbSession,
    settings: AppSettings,
    path: str = Query(default="", max_length=2048),
    include_hidden: bool = False,
) -> list[FileEntry]:
    """List one directory without allowing symbolic-link escapes."""
    await require_project_permission(session, user, project_id, "files:read")
    root = await _root_or_404(project_id, session, settings)
    directory = _safe_path(root, path)
    try:
        if not directory.is_dir():
            raise HTTPException(status_code=404, detail="Directory not found")
        entries = await asyncio.to_thread(
            _local_directory_entries,
            root,
            directory,
            include_hidden,
        )
    except PermissionError:
        return await _agent_browse_files(root, path, include_hidden, settings)
    return sorted(
        entries,
        key=lambda item: (
            item.kind != "directory" and item.target_kind != "directory",
            item.name.lower(),
        ),
    )


@router.post("", response_model=FileEntry, status_code=status.HTTP_201_CREATED)
async def create_file_entry(
    project_id: str,
    data: FileCreateRequest,
    user: CurrentUser,
    session: DbSession,
    settings: AppSettings,
) -> FileEntry:
    """Create a text file or directory without overwriting existing data."""
    project = await require_project_permission(session, user, project_id, "files:write")
    await assert_minecraft_idle(session, project)
    if project.project_type in BUKKIT_TYPES and project.status == "running":
        raise HTTPException(
            status_code=409, detail="Stop Minecraft before changing server files"
        )
    root = await _root_or_404(project_id, session, settings)
    target = _safe_path(root, data.path)
    _guard_minecraft_file(project, root, target)
    if target.exists() or target.is_symlink():
        raise HTTPException(status_code=409, detail="Path already exists")
    target.parent.mkdir(parents=True, exist_ok=True)
    if data.kind == "directory":
        target.mkdir(mode=0o750)
    else:
        target.write_text(data.content, encoding="utf-8")
        os.chmod(target, 0o640)
    await write_audit_log(
        session,
        action="file.create",
        actor_id=user.id,
        resource_type="project",
        resource_id=project_id,
        details={"path": data.path, "kind": data.kind},
    )
    await session.commit()
    return _entry(root, target)


@router.put("/content", response_model=FileEntry)
async def write_text_file(
    project_id: str,
    data: FileWriteRequest,
    user: CurrentUser,
    session: DbSession,
    settings: AppSettings,
    path: str = Query(min_length=1, max_length=2048),
) -> FileEntry:
    """Atomically replace a bounded UTF-8 text file."""
    project = await require_project_permission(session, user, project_id, "files:write")
    await assert_minecraft_idle(session, project)
    if project.project_type in BUKKIT_TYPES and project.status == "running":
        raise HTTPException(
            status_code=409, detail="Stop Minecraft before changing server files"
        )
    root = await _root_or_404(project_id, session, settings)
    target = _safe_path(root, path)
    _guard_minecraft_file(project, root, target)
    if not target.is_file() or target.is_symlink():
        raise HTTPException(status_code=404, detail="Text file not found")
    temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(data.content, encoding="utf-8")
    os.chmod(temporary, target.stat().st_mode & 0o777)
    temporary.replace(target)
    await write_audit_log(
        session,
        action="file.edit",
        actor_id=user.id,
        resource_type="project",
        resource_id=project_id,
        details={"path": path, "size_bytes": len(data.content.encode("utf-8"))},
    )
    await session.commit()
    return _entry(root, target)


@router.get("/content")
async def read_text_file(
    project_id: str,
    user: CurrentUser,
    session: DbSession,
    settings: AppSettings,
    path: str = Query(min_length=1, max_length=2048),
) -> dict[str, str]:
    """Read a bounded UTF-8 text file."""
    project = await require_project_permission(session, user, project_id, "files:read")
    root = await _root_or_404(project_id, session, settings)
    target = _safe_path(root, path)
    if not target.is_file() or target.is_symlink():
        raise HTTPException(status_code=404, detail="Text file not found")
    if target.stat().st_size > 2 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="File is too large for text editing")
    try:
        content = target.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=415, detail="File is not UTF-8 text") from exc
    if project.runtime_type in BUKKIT_TYPES and target == root / "server.properties":
        from app.api.routes.minecraft import _mask_properties

        content = _mask_properties(content)
    return {"path": path, "content": content}


@router.post("/move", response_model=FileEntry)
async def move_file_entry(
    project_id: str,
    data: FileMoveRequest,
    user: CurrentUser,
    session: DbSession,
    settings: AppSettings,
    path: str = Query(min_length=1, max_length=2048),
) -> FileEntry:
    """Atomically move a file or directory inside the project root."""
    project = await require_project_permission(session, user, project_id, "files:write")
    await assert_minecraft_idle(session, project)
    if project.project_type in BUKKIT_TYPES and project.status == "running":
        raise HTTPException(
            status_code=409, detail="Stop Minecraft before changing server files"
        )
    root = await _root_or_404(project_id, session, settings)
    source = _safe_path(root, path)
    destination = _safe_path(root, data.destination)
    _guard_minecraft_file(project, root, source)
    _guard_minecraft_file(project, root, destination)
    try:
        _move_local_path(source, destination)
        entry = _entry(root, destination)
    except PermissionError:
        try:
            result = await AgentClient(settings).execute(
                "move_path",
                {
                    "root": str(root),
                    "source": path,
                    "destination": data.destination,
                },
                request_timeout=120,
            )
            entry = FileEntry.model_validate(result.get("entry"))
        except Exception as exc:
            raise HTTPException(
                status_code=502,
                detail="Unable to move project path",
            ) from exc
    await write_audit_log(
        session,
        action="file.move",
        actor_id=user.id,
        resource_type="project",
        resource_id=project_id,
        details={"source": path, "destination": data.destination},
    )
    await session.commit()
    return entry


@router.delete("", response_model=MessageResponse)
async def delete_file_entry(
    project_id: str,
    data: FileDeleteRequest,
    user: CurrentUser,
    session: DbSession,
    settings: AppSettings,
    path: str = Query(min_length=1, max_length=2048),
) -> MessageResponse:
    """Delete a confirmed path without following symbolic links."""
    project = await require_project_permission(session, user, project_id, "files:write")
    await assert_minecraft_idle(session, project)
    if project.project_type in BUKKIT_TYPES and project.status == "running":
        raise HTTPException(
            status_code=409, detail="Stop Minecraft before changing server files"
        )
    if data.confirm_path != path:
        raise HTTPException(status_code=422, detail="Path confirmation mismatch")
    root = await _root_or_404(project_id, session, settings)
    target = _safe_delete_path(root, path)
    _guard_minecraft_file(project, root, target)
    if target == root:
        raise HTTPException(status_code=422, detail="Project root cannot be deleted")
    try:
        await _delete_local_path(target)
    except PermissionError:
        try:
            await AgentClient(settings).execute(
                "delete_path",
                {"root": str(root), "path": path},
                request_timeout=120,
            )
        except Exception as exc:
            raise HTTPException(
                status_code=502,
                detail="Unable to delete project path",
            ) from exc
    await write_audit_log(
        session,
        action="file.delete",
        actor_id=user.id,
        resource_type="project",
        resource_id=project_id,
        details={"path": path},
    )
    await session.commit()
    return MessageResponse(message="Path deleted")


@router.post("/upload", response_model=FileEntry)
async def upload_file(
    project_id: str,
    file: UploadFile,
    user: CurrentUser,
    session: DbSession,
    settings: AppSettings,
    path: str = Query(min_length=1, max_length=2048),
) -> FileEntry:
    """Stream an upload to a temporary file and atomically publish it."""
    project = await require_project_permission(session, user, project_id, "files:write")
    await assert_minecraft_idle(session, project)
    if project.project_type in BUKKIT_TYPES and project.status == "running":
        raise HTTPException(
            status_code=409, detail="Stop Minecraft before changing server files"
        )
    root = await _root_or_404(project_id, session, settings)
    target = _safe_path(root, path)
    _guard_minecraft_file(project, root, target)
    try:
        if target.exists() or target.is_symlink():
            raise HTTPException(status_code=409, detail="Destination already exists")
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.upload")
        total = 0
        try:
            async with await anyio.open_file(temporary, "wb") as handle:
                while chunk := await file.read(1024 * 1024):
                    total += len(chunk)
                    if total > settings.max_upload_bytes:
                        raise HTTPException(status_code=413, detail="Upload exceeds size limit")
                    await handle.write(chunk)
            os.chmod(temporary, 0o640)
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
        entry = _entry(root, target)
    except PermissionError:
        entry, total = await _publish_upload_with_agent(root, path, file, settings)
    finally:
        await file.close()
    await write_audit_log(
        session,
        action="file.upload",
        actor_id=user.id,
        resource_type="project",
        resource_id=project_id,
        details={"path": path, "size_bytes": total},
    )
    await session.commit()
    return entry


@router.get("/download", response_class=FileResponse)
async def download_file(
    project_id: str,
    user: CurrentUser,
    session: DbSession,
    settings: AppSettings,
    path: str = Query(min_length=1, max_length=2048),
) -> FileResponse:
    """Download one regular project file."""
    project = await require_project_permission(session, user, project_id, "files:read")
    root = await _root_or_404(project_id, session, settings)
    target = _safe_path(root, path)
    if project.runtime_type in BUKKIT_TYPES and target == root / "server.properties":
        raise HTTPException(status_code=409, detail="Use the Minecraft configuration editor")
    try:
        return _local_download_response(target)
    except PermissionError:
        download_id = uuid.uuid4().hex
        try:
            result = await AgentClient(settings).execute(
                "stage_file_download",
                {"root": str(root), "path": path, "download_id": download_id},
                request_timeout=300,
            )
        except httpx.HTTPStatusError as exc:
            status_code = 404 if exc.response.status_code == 422 else 502
            raise HTTPException(status_code=status_code, detail="File not found") from exc
        staged_path = result.get("path")
        if not isinstance(staged_path, str):
            raise HTTPException(
                status_code=502,
                detail="Invalid agent download response",
            ) from None
        return _staged_download_response(
            staged_path,
            download_id,
            target.name,
            settings,
        )


@router.post("/archive", response_model=FileJobResponse)
async def archive_files(
    project_id: str,
    data: ArchiveRequest,
    user: CurrentUser,
    session: DbSession,
    settings: AppSettings,
) -> FileJobResponse:
    """Queue ZIP or TAR.GZ creation inside the project root."""
    project = await require_project_permission(session, user, project_id, "files:write")
    await assert_minecraft_idle(session, project)
    if project.project_type in BUKKIT_TYPES and project.status == "running":
        raise HTTPException(
            status_code=409, detail="Stop Minecraft before changing server files"
        )
    root = await _root_or_404(project_id, session, settings)
    source = _safe_path(root, data.source)
    destination = _safe_path(root, data.destination)
    if not _is_supported_archive(destination):
        raise HTTPException(status_code=422, detail="Destination must end with .zip or .tar.gz")
    try:
        _validate_local_archive_paths(source, destination)
    except PermissionError:
        pass
    job = Job(
        kind="files.archive",
        payload={"project_id": project_id, "destination": data.destination},
    )
    session.add(job)
    await reserve_minecraft_job(session, project, job)
    operation = FileOperation(
        project_id=project_id,
        operation="archive",
        path=data.source,
        status="queued",
    )
    session.add(operation)
    await write_audit_log(
        session,
        action="file.archive",
        actor_id=user.id,
        resource_type="project",
        resource_id=project_id,
        details={"source": data.source, "destination": data.destination},
    )
    await session.commit()
    enqueue(
        "files.archive",
        task_id=job.id,
        kwargs={
            "project_root": str(root),
            "source": data.source,
            "destination": data.destination,
        },
    )
    return FileJobResponse(job_id=job.id)


@router.post("/extract", response_model=FileJobResponse)
async def extract_archive(
    project_id: str,
    data: ArchiveRequest,
    user: CurrentUser,
    session: DbSession,
    settings: AppSettings,
) -> FileJobResponse:
    """Queue validated ZIP or TAR.GZ extraction inside the project root."""
    project = await require_project_permission(session, user, project_id, "files:write")
    await assert_minecraft_idle(session, project)
    if project.project_type in BUKKIT_TYPES and project.status == "running":
        raise HTTPException(
            status_code=409, detail="Stop Minecraft before changing server files"
        )
    root = await _root_or_404(project_id, session, settings)
    source = _safe_path(root, data.source)
    _safe_path(root, data.destination)
    if not _is_supported_archive(source):
        raise HTTPException(status_code=422, detail="Source must be a ZIP or TAR.GZ file")
    try:
        if not source.is_file() or source.is_symlink():
            raise HTTPException(status_code=422, detail="Source must be a ZIP or TAR.GZ file")
    except PermissionError:
        pass
    job = Job(
        kind="files.extract",
        payload={"project_id": project_id, "source": data.source},
    )
    session.add(job)
    await reserve_minecraft_job(session, project, job)
    await write_audit_log(
        session,
        action="file.extract",
        actor_id=user.id,
        resource_type="project",
        resource_id=project_id,
        details={"source": data.source, "destination": data.destination},
    )
    await session.commit()
    enqueue(
        "files.extract",
        task_id=job.id,
        kwargs={
            "project_root": str(root),
            "source": data.source,
            "destination": data.destination,
        },
    )
    return FileJobResponse(job_id=job.id)
