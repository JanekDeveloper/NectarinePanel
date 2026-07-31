"""RBAC and project membership tests."""

from typing import Any

from fastapi.testclient import TestClient

RBAC_TEST_PASSWORD = "correct-horse-rbac"  # noqa: S105 - test credential.


def _project(client: TestClient, headers: dict[str, str], name: str) -> dict[str, Any]:
    """Create and return a project for RBAC tests."""
    response = client.post(
        "/api/v1/projects",
        headers=headers,
        json={
            "name": name,
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
    password: str = RBAC_TEST_PASSWORD,
) -> dict[str, Any]:
    """Create and return a user for RBAC tests."""
    response = client.post(
        "/api/v1/users",
        headers=headers,
        json={"username": username, "password": password, "role": role},
    )
    assert response.status_code == 201
    return response.json()


def _login(
    client: TestClient,
    username: str,
    password: str = RBAC_TEST_PASSWORD,
) -> dict[str, str]:
    """Authenticate a test user and return bearer headers."""
    response = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password},
    )
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_existing_admin_is_owner(client: TestClient, auth_headers: dict[str, str]) -> None:
    """The legacy installer account is exposed as an owner."""
    response = client.get("/api/v1/auth/me", headers=auth_headers)

    assert response.status_code == 200
    assert response.json()["role"] == "owner"


def test_user_endpoints_require_auth(client: TestClient) -> None:
    """User management endpoints require a bearer token."""
    response = client.get("/api/v1/users")

    assert response.status_code == 401


def test_last_active_owner_cannot_be_disabled(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    """The last active owner cannot be disabled."""
    owner = client.get("/api/v1/auth/me", headers=auth_headers).json()

    response = client.post(f"/api/v1/users/{owner['id']}/disable", headers=auth_headers)

    assert response.status_code == 409
    assert response.json()["detail"] == "Cannot disable the last active owner"


def test_admin_cannot_disable_owner(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    """Admins can manage delegated users but cannot manage owners."""
    owner = client.get("/api/v1/auth/me", headers=auth_headers).json()
    _user(client, auth_headers, "panel-admin", "admin")
    admin_headers = _login(client, "panel-admin")

    response = client.post(f"/api/v1/users/{owner['id']}/disable", headers=admin_headers)

    assert response.status_code == 403


def test_project_membership_limits_project_access(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    """Project-scoped users can only see assigned projects."""
    first = _project(client, auth_headers, "Assigned API")
    second = _project(client, auth_headers, "Hidden API")
    viewer = _user(client, auth_headers, "viewer", "viewer")
    member = client.put(
        f"/api/v1/projects/{first['id']}/members/{viewer['id']}",
        headers=auth_headers,
        json={"role": "viewer"},
    )
    assert member.status_code == 200
    viewer_headers = _login(client, "viewer")

    listed = client.get("/api/v1/projects", headers=viewer_headers)
    assigned = client.get(f"/api/v1/projects/{first['id']}", headers=viewer_headers)
    hidden = client.get(f"/api/v1/projects/{second['id']}", headers=viewer_headers)

    assert listed.status_code == 200
    assert [project["id"] for project in listed.json()] == [first["id"]]
    assert assigned.status_code == 200
    assert hidden.status_code == 403


def test_viewer_cannot_mutate_project_files_env_or_runtime(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    """Viewer memberships are read-only."""
    project = _project(client, auth_headers, "Viewer API")
    viewer = _user(client, auth_headers, "readonly", "viewer")
    member = client.put(
        f"/api/v1/projects/{project['id']}/members/{viewer['id']}",
        headers=auth_headers,
        json={"role": "viewer"},
    )
    assert member.status_code == 200
    viewer_headers = _login(client, "readonly")

    patch = client.patch(
        f"/api/v1/projects/{project['id']}",
        headers=viewer_headers,
        json={"description": "mutated"},
    )
    file_create = client.post(
        f"/api/v1/projects/{project['id']}/files",
        headers=viewer_headers,
        json={"path": "app.txt", "kind": "file", "content": "value"},
    )
    env_put = client.put(
        f"/api/v1/projects/{project['id']}/env/API_TOKEN",
        headers=viewer_headers,
        json={"key": "API_TOKEN", "value": "secret-value", "is_secret": True},
    )
    runtime = client.post(
        f"/api/v1/projects/{project['id']}/runtime/start",
        headers=viewer_headers,
    )

    assert patch.status_code == 403
    assert file_create.status_code == 403
    assert env_put.status_code == 403
    assert runtime.status_code == 403


def test_maintainer_can_start_assigned_project_but_cannot_reveal_secret(
    client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch: Any,
) -> None:
    """Maintainers can operate assigned projects without revealing secret values."""
    from app.services.agent import AgentClient

    async def fake_execute(
        self: AgentClient,
        operation: str,
        parameters: dict[str, object] | None = None,
        *,
        request_timeout: float = 30,
    ) -> dict[str, object]:
        """Return a successful runtime operation."""
        del self, operation, parameters, request_timeout
        return {"ok": True}

    monkeypatch.setattr(AgentClient, "execute", fake_execute)
    project = _project(client, auth_headers, "Maintained API")
    maintainer = _user(client, auth_headers, "maintainer", "maintainer")
    assert (
        client.put(
            f"/api/v1/projects/{project['id']}/members/{maintainer['id']}",
            headers=auth_headers,
            json={"role": "maintainer"},
        ).status_code
        == 200
    )
    assert (
        client.put(
            f"/api/v1/projects/{project['id']}/env/API_TOKEN",
            headers=auth_headers,
            json={"key": "API_TOKEN", "value": "secret-value", "is_secret": True},
        ).status_code
        == 200
    )
    maintainer_headers = _login(client, "maintainer")

    started = client.post(
        f"/api/v1/projects/{project['id']}/runtime/start",
        headers=maintainer_headers,
    )
    reveal = client.post(
        f"/api/v1/projects/{project['id']}/env/API_TOKEN/reveal",
        headers=maintainer_headers,
        json={"confirm_key": "API_TOKEN"},
    )

    assert started.status_code == 200
    assert reveal.status_code == 403


def test_user_audit_does_not_contain_passwords(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    """User audit events do not expose plaintext passwords."""
    _user(client, auth_headers, "audited-user", "viewer", password="audit-password-123")

    audit = client.get("/api/v1/audit-logs", headers=auth_headers)

    assert audit.status_code == 200
    assert "user.create" in audit.text
    assert "audit-password-123" not in audit.text
