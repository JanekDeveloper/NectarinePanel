"""Project file manager API tests."""

import asyncio
from io import BytesIO
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient


def _project(client: TestClient, headers: dict[str, str]) -> str:
    """Create a file-manager test project."""
    response = client.post(
        "/api/v1/projects",
        headers=headers,
        json={
            "name": "Files Project",
            "project_type": "docker",
            "runtime_type": "docker",
        },
    )
    assert response.status_code == 201
    return response.json()["id"]


def test_file_lifecycle(client: TestClient, auth_headers: dict[str, str]) -> None:
    """Owner can create, edit, move, read, and delete a project file."""
    project_id = _project(client, auth_headers)
    created = client.post(
        f"/api/v1/projects/{project_id}/files",
        headers=auth_headers,
        json={"path": "config/app.txt", "kind": "file", "content": "initial"},
    )
    assert created.status_code == 201
    edited = client.put(
        f"/api/v1/projects/{project_id}/files/content",
        params={"path": "config/app.txt"},
        headers=auth_headers,
        json={"content": "updated"},
    )
    assert edited.status_code == 200
    moved = client.post(
        f"/api/v1/projects/{project_id}/files/move",
        params={"path": "config/app.txt"},
        headers=auth_headers,
        json={"destination": "config/runtime.txt"},
    )
    assert moved.status_code == 200
    read = client.get(
        f"/api/v1/projects/{project_id}/files/content",
        params={"path": "config/runtime.txt"},
        headers=auth_headers,
    )
    assert read.json()["content"] == "updated"
    deleted = client.request(
        "DELETE",
        f"/api/v1/projects/{project_id}/files",
        params={"path": "config/runtime.txt"},
        headers=auth_headers,
        json={"confirm_path": "config/runtime.txt"},
    )
    assert deleted.status_code == 200


def test_file_listing_includes_recursive_directory_size(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    """Directory entries expose their bounded recursive byte size."""
    project_id = _project(client, auth_headers)
    created = client.post(
        f"/api/v1/projects/{project_id}/files",
        headers=auth_headers,
        json={"path": "storage/data.bin", "kind": "file", "content": "123456"},
    )
    assert created.status_code == 201

    response = client.get(
        f"/api/v1/projects/{project_id}/files",
        headers=auth_headers,
    )

    assert response.status_code == 200
    storage = next(item for item in response.json() if item["name"] == "storage")
    assert storage["size_bytes"] == 6


def test_file_listing_opens_internal_directory_symlink(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    """Internal release symlinks expose directory metadata and remain browsable."""
    project_id = _project(client, auth_headers)
    root = Path(f"/tmp/nectarine-panel-tests/projects/{project_id}")
    release = root / "releases" / "release-1"
    release.mkdir(parents=True)
    (release / "app.txt").write_text("deployed", encoding="utf-8")
    (root / "current").symlink_to(release, target_is_directory=True)

    listing = client.get(
        f"/api/v1/projects/{project_id}/files",
        headers=auth_headers,
    )
    current = next(item for item in listing.json() if item["name"] == "current")
    opened = client.get(
        f"/api/v1/projects/{project_id}/files",
        params={"path": "current"},
        headers=auth_headers,
    )

    assert listing.status_code == 200
    assert current["kind"] == "symlink"
    assert current["target_kind"] == "directory"
    assert current["size_bytes"] == len("deployed")
    assert opened.status_code == 200
    assert [item["name"] for item in opened.json()] == ["app.txt"]


def test_file_listing_does_not_classify_external_symlink_target(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    """Symlinks outside the project never become browsable entries."""
    project_id = _project(client, auth_headers)
    root = Path(f"/tmp/nectarine-panel-tests/projects/{project_id}")
    root.mkdir(parents=True)
    outside = Path("/tmp/nectarine-panel-tests/external-directory")
    outside.mkdir(parents=True, exist_ok=True)
    (root / "external").symlink_to(outside, target_is_directory=True)

    listing = client.get(
        f"/api/v1/projects/{project_id}/files",
        headers=auth_headers,
    )
    external = next(item for item in listing.json() if item["name"] == "external")
    opened = client.get(
        f"/api/v1/projects/{project_id}/files",
        params={"path": "external"},
        headers=auth_headers,
    )

    assert external["kind"] == "symlink"
    assert external["target_kind"] is None
    assert external["size_bytes"] == 0
    assert opened.status_code == 422


def test_file_delete_falls_back_to_privileged_agent_on_permission_error(
    client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Protected imported-project entries are deleted through the system agent."""
    from app.api.routes import files
    from app.services.agent import AgentClient

    project_id = _project(client, auth_headers)
    created = client.post(
        f"/api/v1/projects/{project_id}/files",
        headers=auth_headers,
        json={"path": "protected.txt", "kind": "file", "content": "protected"},
    )
    assert created.status_code == 201
    calls: list[tuple[str, dict[str, Any]]] = []

    async def deny_local_delete(target: object) -> None:
        """Simulate an imported path blocked by discretionary access control."""
        del target
        raise PermissionError

    async def fake_execute(
        self: AgentClient,
        operation: str,
        parameters: dict[str, Any] | None = None,
        *,
        request_timeout: float = 30,
    ) -> dict[str, Any]:
        """Capture the privileged deletion request."""
        del self, request_timeout
        calls.append((operation, parameters or {}))
        return {"removed": "protected.txt"}

    monkeypatch.setattr(files, "_delete_local_path", deny_local_delete)
    monkeypatch.setattr(AgentClient, "execute", fake_execute)
    response = client.request(
        "DELETE",
        f"/api/v1/projects/{project_id}/files",
        params={"path": "protected.txt"},
        headers=auth_headers,
        json={"confirm_path": "protected.txt"},
    )

    assert response.status_code == 200
    assert calls == [
        (
            "delete_path",
            {
                "root": f"/tmp/nectarine-panel-tests/projects/{project_id}",
                "path": "protected.txt",
            },
        )
    ]


def test_file_delete_unlinks_symlink_without_touching_target(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    """File deletion never follows the final symbolic link."""
    project_id = _project(client, auth_headers)
    root = Path(f"/tmp/nectarine-panel-tests/projects/{project_id}")
    root.mkdir(parents=True)
    outside = Path("/tmp/nectarine-panel-tests/outside.txt")
    outside.write_text("keep", encoding="utf-8")
    (root / "link").symlink_to(outside)

    response = client.request(
        "DELETE",
        f"/api/v1/projects/{project_id}/files",
        params={"path": "link"},
        headers=auth_headers,
        json={"confirm_path": "link"},
    )

    assert response.status_code == 200
    assert not (root / "link").is_symlink()
    assert outside.read_text(encoding="utf-8") == "keep"


def test_file_move_falls_back_to_privileged_agent_on_permission_error(
    client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Protected imported-project entries are moved through the system agent."""
    from app.api.routes import files
    from app.services.agent import AgentClient

    project_id = _project(client, auth_headers)
    created = client.post(
        f"/api/v1/projects/{project_id}/files",
        headers=auth_headers,
        json={"path": "source.txt", "kind": "file", "content": "payload"},
    )
    assert created.status_code == 201

    def deny_local_move(source: object, destination: object) -> None:
        """Simulate an imported path blocked by discretionary access control."""
        del source, destination
        raise PermissionError

    async def fake_execute(
        self: AgentClient,
        operation: str,
        parameters: dict[str, Any] | None = None,
        *,
        request_timeout: float = 30,
    ) -> dict[str, Any]:
        """Return deterministic metadata for the privileged move."""
        del self, request_timeout
        assert operation == "move_path"
        assert parameters is not None
        return {
            "entry": {
                "name": "source.txt",
                "path": "archive/source.txt",
                "kind": "file",
                "size_bytes": 7,
                "modified_at": "2026-06-30T12:00:00Z",
                "permissions": "-rw-r-----",
            }
        }

    monkeypatch.setattr(files, "_move_local_path", deny_local_move)
    monkeypatch.setattr(AgentClient, "execute", fake_execute)
    response = client.post(
        f"/api/v1/projects/{project_id}/files/move",
        params={"path": "source.txt"},
        headers=auth_headers,
        json={"destination": "archive/source.txt"},
    )

    assert response.status_code == 200
    assert response.json()["path"] == "archive/source.txt"


@pytest.mark.parametrize("filename", ["server.js", "applications.sqlite3"])
def test_file_download_falls_back_to_privileged_agent_on_permission_error(
    client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    filename: str,
) -> None:
    """Protected imported-project files are staged through the system agent."""
    from app.services.agent import AgentClient

    project_id = _project(client, auth_headers)
    created = client.post(
        f"/api/v1/projects/{project_id}/files",
        headers=auth_headers,
        json={"path": filename, "kind": "file", "content": "payload"},
    )
    assert created.status_code == 201

    original_open = Path.open

    def deny_file_open(target: Path, *args: Any, **kwargs: Any) -> Any:
        """Allow metadata access but reject opening the protected source file."""
        if target.name == filename:
            raise PermissionError
        return original_open(target, *args, **kwargs)

    async def fake_execute(
        self: AgentClient,
        operation: str,
        parameters: dict[str, Any] | None = None,
        *,
        request_timeout: float = 30,
    ) -> dict[str, Any]:
        """Create the deterministic staged file returned by the agent."""
        del self
        assert operation == "stage_file_download"
        assert request_timeout == 300
        assert parameters is not None
        root = Path(str(parameters["root"]))
        staged = root.parents[1] / ".downloads" / str(parameters["download_id"])
        staged.parent.mkdir(parents=True, exist_ok=True)
        staged.write_bytes(b"payload")
        return {"path": str(staged), "size_bytes": 7}

    monkeypatch.setattr(Path, "open", deny_file_open)
    monkeypatch.setattr(AgentClient, "execute", fake_execute)

    response = client.get(
        f"/api/v1/projects/{project_id}/files/download",
        params={"path": filename},
        headers=auth_headers,
    )

    assert response.status_code == 200
    assert response.content == b"payload"


def test_file_upload_stages_content_before_agent_publish(
    client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Protected uploads are staged and delegated without exposing file bytes to API RPC."""
    from app.api.routes import files
    from app.core.config import get_settings
    from app.services.agent import AgentClient

    project_id = _project(client, auth_headers)

    async def fake_execute(
        self: AgentClient,
        operation: str,
        parameters: dict[str, Any] | None = None,
        *,
        request_timeout: float = 30,
    ) -> dict[str, Any]:
        """Validate the staged-file agent request."""
        del self
        assert operation == "publish_staged_upload"
        assert request_timeout == 300
        assert parameters is not None
        staged = Path(str(parameters["staged_path"]))
        assert staged.read_bytes() == b"payload"
        return {"size_bytes": 7, "modified_at": "2026-07-30T00:00:00+00:00"}

    monkeypatch.setattr(AgentClient, "execute", fake_execute)

    from starlette.datastructures import UploadFile

    upload = UploadFile(filename="app.txt", file=BytesIO(b"payload"))
    entry, size = asyncio.run(
        files._publish_upload_with_agent(
            Path(f"/tmp/nectarine-panel-tests/projects/{project_id}"),
            "app.txt",
            upload,
            get_settings(),
        )
    )

    assert size == 7
    assert entry.path == "app.txt"


def test_file_archive_defers_protected_path_checks_to_agent(
    client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Protected imported paths queue archival for the privileged worker path."""
    from app.api.routes import files

    project_id = _project(client, auth_headers)
    queued: list[dict[str, object]] = []

    def deny_local_validation(source: Path, destination: Path) -> None:
        """Simulate metadata blocked by discretionary access control."""
        del source, destination
        raise PermissionError

    monkeypatch.setattr(files, "_validate_local_archive_paths", deny_local_validation)
    monkeypatch.setattr(
        files,
        "enqueue",
        lambda name, task_id, kwargs: queued.append(
            {"name": name, "task_id": task_id, "kwargs": kwargs}
        ),
    )

    response = client.post(
        f"/api/v1/projects/{project_id}/files/archive",
        headers=auth_headers,
        json={"source": "src", "destination": "src.zip"},
    )

    assert response.status_code == 200
    assert queued[0]["name"] == "files.archive"


def test_file_api_rejects_path_traversal(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """Every file endpoint rejects paths outside the project root."""
    project_id = _project(client, auth_headers)
    response = client.post(
        f"/api/v1/projects/{project_id}/files",
        headers=auth_headers,
        json={"path": "../../escape.txt", "kind": "file", "content": "unsafe"},
    )
    assert response.status_code == 422


def test_tar_gz_archive_is_queued_outside_source(
    client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TAR.GZ creation is supported but cannot target its source directory."""
    from app.api.routes import files

    queued: list[dict[str, object]] = []
    monkeypatch.setattr(
        files,
        "enqueue",
        lambda name, task_id, kwargs: queued.append(
            {"name": name, "task_id": task_id, "kwargs": kwargs}
        ),
    )
    project_id = _project(client, auth_headers)
    created = client.post(
        f"/api/v1/projects/{project_id}/files",
        headers=auth_headers,
        json={"path": "data", "kind": "directory"},
    )
    assert created.status_code == 201
    nested = client.post(
        f"/api/v1/projects/{project_id}/files/archive",
        headers=auth_headers,
        json={"source": "data", "destination": "data/archive.tar.gz"},
    )
    assert nested.status_code == 422
    accepted = client.post(
        f"/api/v1/projects/{project_id}/files/archive",
        headers=auth_headers,
        json={"source": "data", "destination": "data.tar.gz"},
    )
    assert accepted.status_code == 200
    assert queued[0]["name"] == "files.archive"
    assert queued[0]["kwargs"] == {
        "project_root": f"/tmp/nectarine-panel-tests/projects/{project_id}",
        "source": "data",
        "destination": "data.tar.gz",
    }
