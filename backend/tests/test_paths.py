"""Filesystem containment tests."""

from pathlib import Path

import pytest
from app.services.paths import UnsafePathError, resolve_within, runtime_root


@pytest.mark.parametrize("path", ["../secret", "a/../../../etc/passwd", "/../../root"])
def test_resolve_within_rejects_traversal(tmp_path: Path, path: str) -> None:
    """User-controlled paths cannot escape their root."""
    with pytest.raises(UnsafePathError):
        resolve_within(tmp_path, path)


def test_resolve_within_accepts_nested_path(tmp_path: Path) -> None:
    """Normal project paths resolve below their root."""
    assert resolve_within(tmp_path, "files/app.py") == tmp_path / "files" / "app.py"


def test_runtime_root_accepts_imported_source_path() -> None:
    """Imported projects use their validated source directory."""

    class Project:
        """Minimal imported project path source."""

        id = "11111111-1111-1111-1111-111111111111"
        project_type = "backend"
        runtime_config = {"source_path": "/home/example/app"}

    assert runtime_root(Path("/srv/vps-panel"), Project()) == Path("/home/example/app")


def test_runtime_root_rejects_unsafe_imported_source_path() -> None:
    """Imported source paths cannot expose system configuration directories."""

    class Project:
        """Minimal unsafe imported project path source."""

        id = "11111111-1111-1111-1111-111111111111"
        project_type = "backend"
        runtime_config = {"source_path": "/etc"}

    with pytest.raises(UnsafePathError):
        runtime_root(Path("/srv/vps-panel"), Project())
