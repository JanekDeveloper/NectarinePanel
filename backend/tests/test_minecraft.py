"""Minecraft Forge management endpoint tests."""

from pathlib import Path

import pytest
from app.services.minecraft import (
    parse_forge_catalog,
    recommended_java_version,
)
from fastapi.testclient import TestClient


def create_minecraft_project(client: TestClient, headers: dict[str, str]) -> dict[str, object]:
    """Create and return a Minecraft Forge test project."""
    response = client.post(
        "/api/v1/projects",
        headers=headers,
        json={
            "name": "Forge Server",
            "description": "Modded server",
            "project_type": "minecraft_forge",
            "runtime_type": "minecraft_forge",
            "branch": "main",
        },
    )
    assert response.status_code == 201
    return response.json()


def test_minecraft_text_files_validate_json_lists(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    """Whitelist and ops editors accept only Minecraft JSON arrays."""
    project = create_minecraft_project(client, auth_headers)
    project_id = str(project["id"])
    invalid = client.put(
        f"/api/v1/projects/{project_id}/minecraft/files/whitelist.json",
        headers=auth_headers,
        json={"content": '{"name":"Steve"}'},
    )
    assert invalid.status_code == 422
    valid = client.put(
        f"/api/v1/projects/{project_id}/minecraft/files/whitelist.json",
        headers=auth_headers,
        json={"content": '[{"name":"Steve","uuid":"00000000-0000-0000-0000-000000000000"}]'},
    )
    assert valid.status_code == 200
    assert "Steve" in valid.json()["content"]


def test_minecraft_mods_list_only_jars(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    """Mods endpoint exposes only regular JAR files from the mods directory."""
    project = create_minecraft_project(client, auth_headers)
    project_id = str(project["id"])
    mods = Path("/tmp/nectarine-panel-tests/minecraft") / project_id / "mods"
    mods.mkdir(parents=True)
    (mods / "example.jar").write_bytes(b"jar")
    (mods / "readme.txt").write_text("skip", encoding="utf-8")
    response = client.get(
        f"/api/v1/projects/{project_id}/minecraft/mods",
        headers=auth_headers,
    )
    assert response.status_code == 200
    assert response.json() == [
        {
            "name": "example.jar",
            "path": "mods/example.jar",
            "size_bytes": 3,
        }
    ]


def test_minecraft_install_queues_uploaded_forge_installer(
    client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Forge installation accepts only an existing project-local installer."""
    from app.api.routes import minecraft

    queued: list[tuple[str, str, dict[str, object]]] = []
    monkeypatch.setattr(
        minecraft,
        "enqueue",
        lambda name, task_id, kwargs: queued.append((name, task_id, kwargs)),
    )
    project = create_minecraft_project(client, auth_headers)
    project_id = str(project["id"])
    root = Path("/tmp/nectarine-panel-tests/minecraft") / project_id
    root.mkdir(parents=True, exist_ok=True)
    installer = "forge-1.20.1-47.4.0-installer.jar"
    (root / installer).write_bytes(b"installer")
    response = client.post(
        f"/api/v1/projects/{project_id}/minecraft/install",
        headers=auth_headers,
        json={"installer_jar": installer},
    )
    assert response.status_code == 202
    assert response.json()["kind"] == "minecraft.install"
    assert queued[0][0] == "minecraft.install"
    assert queued[0][2] == {
        "project_id": project_id,
        "java_version": 21,
        "installer_jar": installer,
        "minecraft_version": None,
        "forge_version": None,
    }


def test_forge_catalog_groups_stable_versions() -> None:
    """Forge metadata is grouped, sorted, and mapped to required Java versions."""
    catalog = parse_forge_catalog(
        """
        <metadata><versioning><versions>
          <version>1.20.1-47.3.1</version>
          <version>1.21.1-52.0.2</version>
          <version>1.20.1-47.4.0</version>
          <version>1.20.1-47.4.0-custom</version>
        </versions></versioning></metadata>
        """
    )
    assert catalog == [
        {
            "minecraft_version": "1.21.1",
            "forge_versions": ["52.0.2"],
            "java_version": 21,
        },
        {
            "minecraft_version": "1.20.1",
            "forge_versions": ["47.4.0", "47.3.1"],
            "java_version": 17,
        },
    ]
    assert recommended_java_version("1.16.5") == 8
    assert recommended_java_version("1.17.1") == 16


def test_minecraft_auto_install_validates_and_queues_official_version(
    client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Automatic installation accepts only a pair in the official catalog."""
    from app.api.routes import minecraft

    queued: list[tuple[str, str, dict[str, object]]] = []

    async def fake_catalog() -> list[dict[str, object]]:
        """Return a deterministic official Forge catalog."""
        return [
            {
                "minecraft_version": "1.20.1",
                "forge_versions": ["47.4.0", "47.3.1"],
                "java_version": 17,
            }
        ]

    monkeypatch.setattr(minecraft, "forge_version_catalog", fake_catalog)
    monkeypatch.setattr(
        minecraft,
        "enqueue",
        lambda name, task_id, kwargs: queued.append((name, task_id, kwargs)),
    )
    project = create_minecraft_project(client, auth_headers)
    project_id = str(project["id"])

    catalog = client.get(
        f"/api/v1/projects/{project_id}/minecraft/versions",
        headers=auth_headers,
    )
    assert catalog.status_code == 200
    assert catalog.json()["versions"][0]["minecraft_version"] == "1.20.1"

    unsupported = client.post(
        f"/api/v1/projects/{project_id}/minecraft/install",
        headers=auth_headers,
        json={"minecraft_version": "1.20.1", "forge_version": "47.0.0"},
    )
    assert unsupported.status_code == 422

    response = client.post(
        f"/api/v1/projects/{project_id}/minecraft/install",
        headers=auth_headers,
        json={"minecraft_version": "1.20.1", "forge_version": "47.4.0"},
    )
    assert response.status_code == 202
    assert queued[0][2] == {
        "project_id": project_id,
        "java_version": 17,
        "installer_jar": None,
        "minecraft_version": "1.20.1",
        "forge_version": "47.4.0",
    }
