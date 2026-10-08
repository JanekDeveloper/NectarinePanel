"""Plugin server API, release catalog and operation isolation regression tests."""

from pathlib import Path
from typing import Any

import pytest
from app.api.routes import minecraft
from app.services import minecraft_catalogs
from app.services.minecraft_catalogs import java_for_engine
from fastapi.testclient import TestClient


def create_server(client: TestClient, headers: dict[str, str], engine: str = "paper") -> str:
    """Create an isolated plugin server through the real project API."""
    response = client.post(
        "/api/v1/projects",
        headers=headers,
        json={
            "name": f"Test {engine}",
            "project_type": f"minecraft_{engine}",
            "runtime_type": f"minecraft_{engine}",
        },
    )
    assert response.status_code == 201
    return str(response.json()["id"])


@pytest.mark.parametrize("engine", ["paper", "purpur", "spigot"])
def test_plugin_server_creation_and_type_lock(
    client: TestClient, auth_headers: dict[str, str], engine: str
) -> None:
    """New cores use Minecraft storage and reject generic engine changes."""
    project_id = create_server(client, auth_headers, engine)
    configuration = client.get(f"/api/v1/projects/{project_id}/minecraft", headers=auth_headers)
    assert configuration.status_code == 200
    assert configuration.json()["engine"] == engine
    assert configuration.json()["configuration"]["server_jar"] == "server.jar"
    response = client.patch(
        f"/api/v1/projects/{project_id}", headers=auth_headers, json={"runtime_type": "docker"}
    )
    assert response.status_code == 422
    for field in ("project_type", "runtime_type", "runtime_config"):
        response = client.patch(
            f"/api/v1/projects/{project_id}", headers=auth_headers, json={field: None}
        )
        assert response.status_code == 422
    response = client.post(
        "/api/v1/projects",
        headers=auth_headers,
        json={
            "name": "Mismatched",
            "project_type": f"minecraft_{engine}",
            "runtime_type": "docker",
        },
    )
    assert response.status_code == 422


def test_configuration_protects_metadata_and_masks_rcon(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """Ordinary editors never return the managed RCON secret or replace its keys."""
    project_id = create_server(client, auth_headers)
    root = Path("/tmp/nectarine-panel-tests/minecraft") / project_id
    root.mkdir(parents=True)
    (root / "server.properties").write_text(
        "rcon.password=private-secret\nserver-port=25565\nmotd=Old\n", encoding="utf-8"
    )
    response = client.get(
        f"/api/v1/projects/{project_id}/minecraft/files/server.properties", headers=auth_headers
    )
    assert "private-secret" not in response.text
    response = client.put(
        f"/api/v1/projects/{project_id}/files/content?path=server.properties",
        headers=auth_headers,
        json={"content": "rcon.password=override\n"},
    )
    assert response.status_code == 409
    response = client.put(
        f"/api/v1/projects/{project_id}/minecraft/files/server.properties",
        headers=auth_headers,
        json={"content": "motd=dangerous\\\n"},
    )
    assert response.status_code == 422
    response = client.put(
        f"/api/v1/projects/{project_id}/minecraft/files/server.properties",
        headers=auth_headers,
        json={"content": "server-port=30000\nrcon.password=bad\nmotd=New\n"},
    )
    assert response.status_code == 200
    assert "private-secret" not in response.text
    stored = (root / "server.properties").read_text()
    assert "server-port=25565" in stored
    assert "rcon.password=private-secret" in stored
    assert "motd=New" in stored
    response = client.put(
        f"/api/v1/projects/{project_id}/minecraft/files/server.properties",
        headers=auth_headers,
        json={"content": "server\\u002dport=30000\n"},
    )
    assert response.status_code == 422
    response = client.get(
        f"/api/v1/projects/{project_id}/files/content",
        headers=auth_headers,
        params={"path": "server.properties"},
    )
    assert "private-secret" not in response.text


def test_install_reserves_all_mutation_paths(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """One queued install excludes config, file, backup and duplicate installs."""
    project_id = create_server(client, auth_headers)
    queued = []

    async def artifact(engine: str, version: str, build: str) -> dict[str, Any]:
        """Return deterministic official metadata without a network dependency."""
        return {
            "engine": engine,
            "minecraft_version": version,
            "build_id": build,
            "java_version": 21,
            "url": "https://fill-data.papermc.io/test",
            "checksum": "a" * 64,
            "checksum_algorithm": "sha256",
        }

    monkeypatch.setattr(minecraft, "resolve_artifact", artifact)
    monkeypatch.setattr(minecraft, "enqueue", lambda *args, **kwargs: queued.append(kwargs))
    url = f"/api/v1/projects/{project_id}/minecraft"
    request = {"minecraft_version": "1.21.1", "build_id": "123"}
    response = client.post(url + "/install", headers=auth_headers, json=request)
    assert response.status_code == 202
    assert queued[0]["kwargs"] == {"project_id": project_id}
    assert (
        client.get(url, headers=auth_headers).json()["active_job_id"] == response.json()["id"]
    )
    assert client.post(url + "/install", headers=auth_headers, json=request).status_code == 409
    assert (
        client.put(
            url, headers=auth_headers, json={"eula_accepted": True, "server_jar": "server.jar"}
        ).status_code
        == 409
    )
    assert (
        client.post(
            f"/api/v1/projects/{project_id}/files",
            headers=auth_headers,
            json={"path": "test.txt", "kind": "file", "content": "x"},
        ).status_code
        == 409
    )
    assert (
        client.post(f"/api/v1/projects/{project_id}/backups", headers=auth_headers).status_code
        == 409
    )
    assert (
        client.post(
            f"/api/v1/projects/{project_id}/runtime/start", headers=auth_headers
        ).status_code
        == 409
    )


def test_config_permissions_for_project_viewer(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """Assigned viewers may read metadata but cannot control or mutate a server."""
    project_id = create_server(client, auth_headers)
    response = client.post(
        "/api/v1/users",
        headers=auth_headers,
        json={"username": "mc-viewer", "password": "a-long-test-password", "role": "viewer"},
    )
    assert response.status_code == 201
    user_id = response.json()["id"]
    assert (
        client.put(
            f"/api/v1/projects/{project_id}/members/{user_id}",
            headers=auth_headers,
            json={"role": "viewer"},
        ).status_code
        == 200
    )
    login = client.post(
        "/api/v1/auth/login", json={"username": "mc-viewer", "password": "a-long-test-password"}
    )
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    url = f"/api/v1/projects/{project_id}/minecraft"
    assert client.get(url, headers=headers).status_code == 200
    assert client.put(url, headers=headers, json={"eula_accepted": True}).status_code == 403
    assert client.post(url + "/start", headers=headers).status_code == 403
    assert (
        client.post(
            url + "/players/actions", headers=headers, json={"action": "op", "player": "Steve"}
        ).status_code
        == 403
    )


@pytest.mark.parametrize(
    "engine,version,expected",
    [
        ("paper", "1.7.10", 8),
        ("paper", "1.12.2", 11),
        ("paper", "1.16.5", 16),
        ("paper", "1.18.2", 17),
        ("paper", "1.20.1", 21),
        ("purpur", "26.2", 25),
        ("spigot", "1.16.5", 8),
        ("spigot", "1.20.4", 17),
        ("spigot", "1.20.5", 21),
    ],
)
def test_core_specific_java_policy(engine: str, version: str, expected: int) -> None:
    """Legacy and modern releases retain core-specific Java requirements."""
    assert java_for_engine(engine, version) == expected


@pytest.mark.asyncio
async def test_paper_catalog_filters_experimental_and_snapshots(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Paper installs only numeric release versions and stable-channel builds."""

    async def response(url: str, **kwargs: Any) -> Any:
        """Return the published Fill v3 response structure."""
        if url.endswith("builds"):
            return [
                {"id": 1, "channel": "EXPERIMENTAL", "downloads": {"server:default": {}}},
                {
                    "id": 2,
                    "channel": "STABLE",
                    "downloads": {
                        "server:default": {
                            "url": "https://fill-data.papermc.io/x",
                            "checksums": {"sha256": "a" * 64},
                        }
                    },
                },
            ]
        return {"versions": {"26.2": ["26.2", "26.2-rc-1"], "1.21": ["1.21.1"]}}

    monkeypatch.setattr(minecraft_catalogs, "official_response", response)
    versions = await minecraft_catalogs.server_versions("paper")
    assert [item["minecraft_version"] for item in versions] == ["26.2", "1.21.1"]
    builds = await minecraft_catalogs.server_builds("paper", "26.2")
    assert [build["build_id"] for build in builds] == ["2"]
    artifact = await minecraft_catalogs.resolve_artifact("paper", "26.2", "2")
    assert artifact["checksum_algorithm"] == "sha256"
    with pytest.raises(ValueError):
        await minecraft_catalogs.resolve_artifact("paper", "26.2", "1")


@pytest.mark.parametrize(
    "key", ["rcon.password=", "rcon.password:", r"rcon\u002epassword =", "rcon.\\\n password="]
)
def test_legacy_properties_secret_redaction(key: str) -> None:
    """Imported Java properties cannot expose secrets using alternate syntax."""
    assert "private-secret" not in minecraft._mask_properties(
        f"{key}private-secret\nmotd=Public\n"
    )
    assert "Public" in minecraft._mask_properties(f"{key}private-secret\nmotd=Public\n")


@pytest.mark.asyncio
async def test_purpur_catalog_excludes_failed_builds(monkeypatch: pytest.MonkeyPatch) -> None:
    """Build IDs in the upstream index are checked for successful completion."""

    async def response(url: str, **kwargs: Any) -> dict[str, Any]:
        """Provide an index containing both successful and failed builds."""
        if url.endswith("/1.21.1"):
            return {"builds": {"all": ["1", "2"]}}
        return {"result": "SUCCESS" if url.endswith("/2") else "FAILURE", "md5": "a" * 32}

    monkeypatch.setattr(minecraft_catalogs, "official_response", response)
    builds = await minecraft_catalogs.server_builds("purpur", "1.21.1")
    assert [build["build_id"] for build in builds] == ["2"]
    assert builds[0]["channel"] == "unknown"


def test_failed_enqueue_releases_reserved_server(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A broker outage leaves the project usable and persists a sanitized failure."""
    project_id = create_server(client, auth_headers)
    root = Path("/tmp/nectarine-panel-tests/minecraft") / project_id
    root.mkdir(parents=True)
    (root / "uploaded.jar").write_bytes(b"agent-validates-the-jar")

    def unavailable(*args: Any, **kwargs: Any) -> None:
        """Simulate a disconnected task broker."""
        raise RuntimeError("broker credentials must never leak")

    monkeypatch.setattr(minecraft, "enqueue", unavailable)
    result = client.post(
        f"/api/v1/projects/{project_id}/minecraft/install",
        headers=auth_headers,
        json={"minecraft_version": "1.21.1", "uploaded_jar": "uploaded.jar"},
    )
    assert result.status_code == 503
    assert "credentials" not in result.text
    assert (
        client.get(f"/api/v1/projects/{project_id}/minecraft", headers=auth_headers).json()[
            "active_job_id"
        ]
        is None
    )


def test_rcon_secret_is_managed_and_player_input_is_validated(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """Generic environment CRUD and command injection cannot bypass Minecraft controls."""
    project_id = create_server(client, auth_headers)
    root = f"/api/v1/projects/{project_id}"
    result = client.put(
        root + "/env/MINECRAFT_RCON_PASSWORD",
        headers=auth_headers,
        json={
            "key": "MINECRAFT_RCON_PASSWORD",
            "value": "a-private-secret",
            "is_secret": False,
        },
    )
    assert result.status_code == 409
    assert (
        client.post(
            root + "/minecraft/players/actions",
            headers=auth_headers,
            json={"action": "op", "player": "name\nop attacker"},
        ).status_code
        == 422
    )
    assert (
        client.post(
            root + "/minecraft/players/actions",
            headers=auth_headers,
            json={"action": "op", "player": "ValidPlayer"},
        ).status_code
        == 409
    )
    assert (
        client.post(
            root + "/minecraft/install",
            headers=auth_headers,
            json={
                "minecraft_version": "1.21.1",
                "build_id": "133",
                "url": "https://example.com/server.jar",
            },
        ).status_code
        == 422
    )
