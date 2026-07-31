"""Project resource policy API tests."""

from typing import Any

from fastapi.testclient import TestClient

POLICY_TEST_PASSWORD = "correct-horse-policy"  # noqa: S105 - test credential.


def _project(client: TestClient, headers: dict[str, str]) -> dict[str, Any]:
    """Create and return a resource-policy test project."""
    response = client.post(
        "/api/v1/projects",
        headers=headers,
        json={
            "name": "Guarded API",
            "project_type": "backend",
            "runtime_type": "docker",
        },
    )
    assert response.status_code == 201
    return response.json()


def _user(
    client: TestClient,
    headers: dict[str, str],
    username: str,
    role: str,
) -> dict[str, Any]:
    """Create and return a user for resource-policy tests."""
    response = client.post(
        "/api/v1/users",
        headers=headers,
        json={"username": username, "password": POLICY_TEST_PASSWORD, "role": role},
    )
    assert response.status_code == 201
    return response.json()


def _login(client: TestClient, username: str) -> dict[str, str]:
    """Authenticate a resource-policy test user."""
    response = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": POLICY_TEST_PASSWORD},
    )
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_owner_can_create_and_update_resource_policy(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    """Owners can create and replace project resource policies."""
    project = _project(client, auth_headers)

    created = client.put(
        f"/api/v1/projects/{project['id']}/resource-policy",
        headers=auth_headers,
        json={"enabled": True, "cpu_cores": 1.5, "memory_mb": 512, "disk_mb": 2048},
    )
    fetched = client.get(
        f"/api/v1/projects/{project['id']}/resource-policy",
        headers=auth_headers,
    )

    assert created.status_code == 200
    assert created.json()["cpu_cores"] == 1.5
    assert fetched.status_code == 200
    assert fetched.json()["memory_mb"] == 512


def test_invalid_resource_policy_is_rejected(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    """Enabled policies require at least one bounded resource limit."""
    project = _project(client, auth_headers)

    missing_limit = client.put(
        f"/api/v1/projects/{project['id']}/resource-policy",
        headers=auth_headers,
        json={"enabled": True},
    )
    bad_cpu = client.put(
        f"/api/v1/projects/{project['id']}/resource-policy",
        headers=auth_headers,
        json={"enabled": True, "cpu_cores": 0.01},
    )
    bad_memory = client.put(
        f"/api/v1/projects/{project['id']}/resource-policy",
        headers=auth_headers,
        json={"enabled": True, "memory_mb": 63},
    )
    bad_disk = client.put(
        f"/api/v1/projects/{project['id']}/resource-policy",
        headers=auth_headers,
        json={"enabled": True, "disk_mb": 63},
    )

    assert missing_limit.status_code == 422
    assert bad_cpu.status_code == 422
    assert bad_memory.status_code == 422
    assert bad_disk.status_code == 422


def test_assigned_maintainer_can_read_but_not_update_resource_policy(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    """Assigned maintainers can read policies but cannot mutate them."""
    project = _project(client, auth_headers)
    maintainer = _user(client, auth_headers, "policy-maintainer", "maintainer")
    member = client.put(
        f"/api/v1/projects/{project['id']}/members/{maintainer['id']}",
        headers=auth_headers,
        json={"role": "maintainer"},
    )
    assert member.status_code == 200
    maintainer_headers = _login(client, "policy-maintainer")

    read = client.get(
        f"/api/v1/projects/{project['id']}/resource-policy",
        headers=maintainer_headers,
    )
    update = client.put(
        f"/api/v1/projects/{project['id']}/resource-policy",
        headers=maintainer_headers,
        json={"enabled": True, "memory_mb": 256},
    )

    assert read.status_code == 200
    assert read.json()["enabled"] is False
    assert update.status_code == 403


def test_assigned_viewer_can_read_but_not_update_resource_policy(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    """Assigned viewers can read policies but cannot mutate them."""
    project = _project(client, auth_headers)
    viewer = _user(client, auth_headers, "policy-viewer", "viewer")
    member = client.put(
        f"/api/v1/projects/{project['id']}/members/{viewer['id']}",
        headers=auth_headers,
        json={"role": "viewer"},
    )
    assert member.status_code == 200
    viewer_headers = _login(client, "policy-viewer")

    read = client.get(
        f"/api/v1/projects/{project['id']}/resource-policy",
        headers=viewer_headers,
    )
    update = client.put(
        f"/api/v1/projects/{project['id']}/resource-policy",
        headers=viewer_headers,
        json={"enabled": True, "memory_mb": 256},
    )

    assert read.status_code == 200
    assert update.status_code == 403


def test_admin_can_update_resource_policy(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    """Admins can manage project resource policies."""
    project = _project(client, auth_headers)
    _user(client, auth_headers, "policy-admin", "admin")
    admin_headers = _login(client, "policy-admin")

    response = client.put(
        f"/api/v1/projects/{project['id']}/resource-policy",
        headers=admin_headers,
        json={"enabled": True, "disk_mb": 4096},
    )

    assert response.status_code == 200
    assert response.json()["disk_mb"] == 4096
