"""Safe project filesystem path resolution."""

from pathlib import Path
from typing import Any, Protocol


class UnsafePathError(ValueError):
    """Raised when a user path escapes its allowed root."""


def resolve_within(root: Path, user_path: str) -> Path:
    """Resolve a relative path and enforce containment under root."""
    root_resolved = root.resolve()
    candidate = (root_resolved / user_path.lstrip("/")).resolve()
    if candidate != root_resolved and root_resolved not in candidate.parents:
        raise UnsafePathError("Path escapes the allowed root")
    return candidate


def project_root(storage_root: Path, project_id: str) -> Path:
    """Return a stable project storage root."""
    return resolve_within(storage_root / "projects", project_id)


class ProjectPathSource(Protocol):
    """Minimal project fields required for runtime path selection."""

    id: str
    project_type: str
    runtime_config: dict[str, Any]


def _external_source_root(source_path: str) -> Path:
    """Return a validated imported project source root."""
    candidate = Path(source_path)
    if not candidate.is_absolute():
        raise UnsafePathError("Imported project source path must be absolute")
    resolved = candidate.resolve()
    allowed_roots = [Path("/home"), Path("/opt"), Path("/srv"), Path("/var/www")]
    if not any(resolved == root or root in resolved.parents for root in allowed_roots):
        raise UnsafePathError("Imported project source path is outside allowed roots")
    return resolved


def runtime_root(storage_root: Path, project: ProjectPathSource) -> Path:
    """Return the type-specific project filesystem root."""
    source_path = project.runtime_config.get("source_path")
    if isinstance(source_path, str) and source_path.strip():
        return _external_source_root(source_path)
    category = "minecraft" if project.project_type == "minecraft_forge" else "projects"
    return resolve_within(storage_root / category, project.id)
