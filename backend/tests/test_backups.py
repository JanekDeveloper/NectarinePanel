"""Backup API tests."""

from fastapi.testclient import TestClient


def test_full_backup_endpoint_queues_allowlisted_task(
    client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch,
) -> None:
    """Full-panel backup creation persists metadata and queues the worker task."""
    from app.api.routes import backups

    queued: list[tuple[str, str, dict[str, object]]] = []
    monkeypatch.setattr(
        backups,
        "enqueue",
        lambda name, task_id, kwargs: queued.append((name, task_id, kwargs)),
    )
    response = client.post(
        "/api/v1/backups/full",
        headers=auth_headers,
        json={
            "include_items": ["panel_db", "panel_config", "nginx_configs"],
            "encryption_enabled": False,
        },
    )

    assert response.status_code == 202
    payload = response.json()
    assert payload["backup"]["backup_type"] == "full"
    assert payload["backup"]["status"] == "queued"
    assert queued == [
        (
            "backups.create_full",
            payload["job_id"],
            {
                "backup_id": payload["backup"]["id"],
                "backup_root": "/tmp/nectarine-panel-tests/backups",
                "include_items": ["panel_db", "panel_config", "nginx_configs"],
                "encryption_enabled": False,
            },
        )
    ]
