"""Project cron API tests."""

from fastapi.testclient import TestClient


def test_cron_accepts_runtime_jobs_and_rejects_static(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    """Cron creation supports runtime-backed projects and rejects static sites."""
    systemd_project = client.post(
        "/api/v1/projects",
        headers=auth_headers,
        json={
            "name": "Systemd API",
            "project_type": "backend",
            "runtime_type": "systemd",
            "branch": "main",
        },
    )
    static_project = client.post(
        "/api/v1/projects",
        headers=auth_headers,
        json={
            "name": "Static Site",
            "project_type": "static",
            "runtime_type": "static",
            "branch": "main",
        },
    )
    assert systemd_project.status_code == 201
    assert static_project.status_code == 201

    created = client.post(
        f"/api/v1/projects/{systemd_project.json()['id']}/cron",
        headers=auth_headers,
        json={
            "name": "Hourly task",
            "expression": "0 * * * *",
            "command": "python manage.py task",
            "enabled": True,
        },
    )
    rejected = client.post(
        f"/api/v1/projects/{static_project.json()['id']}/cron",
        headers=auth_headers,
        json={
            "name": "Static task",
            "expression": "0 * * * *",
            "command": "echo no",
            "enabled": True,
        },
    )

    assert created.status_code == 201
    assert created.json()["command"] == "python manage.py task"
    assert rejected.status_code == 409
