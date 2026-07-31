"""Deployment API permission and log tests."""

from typing import Any

from app.models.entities import Project
from fastapi.testclient import TestClient

TEST_PASSWORD = "correct-horse-jobs"  # noqa: S105 - test credential.


def test_bot_runtime_defaults_to_persistent_data_mount() -> None:
    """Existing Docker bot projects receive the safe data mount on deploy."""
    from app.api.routes.jobs import _runtime_config

    project = Project(
        name="Persistent bot",
        slug="persistent-bot",
        project_type="bot",
        runtime_type="docker",
        branch="main",
        runtime_config={"internal_port": 8000},
    )

    assert _runtime_config(project)["persistent_mounts"] == [
        {"source": "data", "target": "/app/data", "read_only": False}
    ]


def test_bot_runtime_preserves_explicit_mount_configuration() -> None:
    """A custom bot mount is never overwritten by the deploy default."""
    from app.api.routes.jobs import _runtime_config

    mounts = [{"source": "state", "target": "/srv/state", "read_only": True}]
    project = Project(
        name="Custom persistent bot",
        slug="custom-persistent-bot",
        project_type="bot",
        runtime_type="docker",
        branch="main",
        runtime_config={"persistent_mounts": mounts},
    )

    assert _runtime_config(project)["persistent_mounts"] == mounts


def _project(
    client: TestClient,
    headers: dict[str, str],
    name: str,
    *,
    repository_url: str | None = "https://github.com/example/api.git",
) -> dict[str, Any]:
    """Create a deployable test project."""
    response = client.post(
        "/api/v1/projects",
        headers=headers,
        json={
            "name": name,
            "project_type": "backend",
            "runtime_type": "docker",
            "repository_url": repository_url,
            "branch": "main",
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
    """Create a test user."""
    response = client.post(
        "/api/v1/users",
        headers=headers,
        json={"username": username, "password": TEST_PASSWORD, "role": role},
    )
    assert response.status_code == 201
    return response.json()


def _login(client: TestClient, username: str) -> dict[str, str]:
    """Authenticate a test user and return headers."""
    response = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": TEST_PASSWORD},
    )
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _assign(
    client: TestClient,
    headers: dict[str, str],
    project_id: str,
    user_id: str,
    role: str,
) -> None:
    """Assign a user to one project."""
    response = client.put(
        f"/api/v1/projects/{project_id}/members/{user_id}",
        headers=headers,
        json={"role": role},
    )
    assert response.status_code == 200


def test_deploy_endpoints_require_project_write_permission(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    """Viewer users cannot deploy, upload archives, rollback, or create webhooks."""
    project = _project(client, auth_headers, "Viewer Deploy API")
    viewer = _user(client, auth_headers, "deploy-viewer", "viewer")
    _assign(client, auth_headers, project["id"], viewer["id"], "viewer")
    viewer_headers = _login(client, "deploy-viewer")

    deploy = client.post(
        f"/api/v1/projects/{project['id']}/deploy",
        headers=viewer_headers,
        json={},
    )
    archive = client.post(
        f"/api/v1/projects/{project['id']}/deploy/archive",
        headers=viewer_headers,
        files={"file": ("release.zip", b"archive", "application/zip")},
    )
    rollback = client.post(
        f"/api/v1/projects/{project['id']}/rollback/not-a-deployment",
        headers=viewer_headers,
    )
    webhook = client.post(
        f"/api/v1/projects/{project['id']}/webhook",
        headers=viewer_headers,
    )

    assert deploy.status_code == 403
    assert archive.status_code == 403
    assert rollback.status_code == 403
    assert webhook.status_code == 403


def test_maintainer_can_queue_assigned_project_deploy(
    client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch: Any,
) -> None:
    """Maintainers can queue deploy jobs for assigned projects."""
    from app.api.routes import jobs

    queued: list[tuple[str, str, dict[str, object]]] = []
    monkeypatch.setattr(
        jobs,
        "enqueue",
        lambda name, task_id, kwargs: queued.append((name, task_id, kwargs)),
    )
    project = _project(client, auth_headers, "Maintainer Deploy API")
    maintainer = _user(client, auth_headers, "deploy-maintainer", "maintainer")
    _assign(client, auth_headers, project["id"], maintainer["id"], "maintainer")
    maintainer_headers = _login(client, "deploy-maintainer")

    response = client.post(
        f"/api/v1/projects/{project['id']}/deploy",
        headers=maintainer_headers,
        json={"revision": "release"},
    )

    assert response.status_code == 200
    assert queued[0][0] == "projects.git_deploy"
    assert queued[0][1] == response.json()["id"]
    assert queued[0][2]["branch"] == "release"


def test_deployment_reads_are_project_scoped(
    client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch: Any,
) -> None:
    """Deployment history, logs, and jobs require read access to the owning project."""
    from app.api.routes import jobs

    monkeypatch.setattr(jobs, "enqueue", lambda name, task_id, kwargs: None)
    first = _project(client, auth_headers, "Visible Deploy API")
    second = _project(client, auth_headers, "Hidden Deploy API")
    response = client.post(
        f"/api/v1/projects/{first['id']}/deploy",
        headers=auth_headers,
        json={},
    )
    assert response.status_code == 200
    job_id = response.json()["id"]
    deployments = client.get(
        f"/api/v1/projects/{first['id']}/deployments",
        headers=auth_headers,
    )
    assert deployments.status_code == 200
    deployment_id = deployments.json()[0]["id"]
    viewer = _user(client, auth_headers, "deploy-scope-viewer", "viewer")
    _assign(client, auth_headers, second["id"], viewer["id"], "viewer")
    viewer_headers = _login(client, "deploy-scope-viewer")

    hidden_history = client.get(
        f"/api/v1/projects/{first['id']}/deployments",
        headers=viewer_headers,
    )
    wrong_project_log = client.get(
        f"/api/v1/projects/{second['id']}/deployments/{deployment_id}/log",
        headers=auth_headers,
    )
    hidden_job = client.get(f"/api/v1/jobs/{job_id}", headers=viewer_headers)
    unauthenticated = client.post(f"/api/v1/projects/{first['id']}/deploy", json={})

    assert hidden_history.status_code == 403
    assert wrong_project_log.status_code == 404
    assert hidden_job.status_code == 403
    assert unauthenticated.status_code == 401
