"""Project CRUD and secret masking tests."""

import hashlib
import hmac
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


def create_project(client: TestClient, headers: dict[str, str]) -> dict[str, object]:
    """Create and return a test project."""
    response = client.post(
        "/api/v1/projects",
        headers=headers,
        json={
            "name": "Example API",
            "description": "Production API",
            "project_type": "backend",
            "runtime_type": "docker",
            "repository_url": "https://github.com/example/api.git",
            "branch": "main",
        },
    )
    assert response.status_code == 201
    return response.json()


def test_project_crud(
    client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Authenticated owner can create, read, update, and delete a project."""
    from app.services.agent import AgentClient

    calls: list[tuple[str, dict[str, object] | None]] = []

    async def fake_execute(
        self: AgentClient,
        operation: str,
        parameters: dict[str, object] | None = None,
        *,
        request_timeout: float = 30,
    ) -> dict[str, object]:
        """Record project cleanup without calling the host agent."""
        del self, request_timeout
        calls.append((operation, parameters))
        return {"project_id": str(parameters["project_id"]) if parameters else ""}

    monkeypatch.setattr(AgentClient, "execute", fake_execute)
    project = create_project(client, auth_headers)
    project_id = project["id"]
    listed = client.get("/api/v1/projects", headers=auth_headers)
    assert listed.status_code == 200
    assert len(listed.json()) == 1
    updated = client.patch(
        f"/api/v1/projects/{project_id}",
        headers=auth_headers,
        json={"description": "Updated"},
    )
    assert updated.status_code == 200
    assert updated.json()["description"] == "Updated"
    rejected = client.request(
        "DELETE",
        f"/api/v1/projects/{project_id}",
        headers=auth_headers,
        json={"confirm_project_name": "wrong"},
    )
    assert rejected.status_code == 409
    deleted = client.request(
        "DELETE",
        f"/api/v1/projects/{project_id}",
        headers=auth_headers,
        json={"confirm_project_name": project["name"]},
    )
    assert deleted.status_code == 204
    assert calls == [
        (
            "cleanup_project",
            {
                "project_id": project_id,
                "runtime_type": "docker",
                "hostnames": [],
            },
        )
    ]


def test_project_delete_keeps_metadata_when_cleanup_fails(
    client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed host cleanup remains retryable and does not orphan metadata."""
    from app.services.agent import AgentClient

    async def fake_execute(
        self: AgentClient,
        operation: str,
        parameters: dict[str, object] | None = None,
        *,
        request_timeout: float = 30,
    ) -> dict[str, object]:
        """Simulate a host cleanup failure."""
        del self, operation, parameters, request_timeout
        raise RuntimeError("agent unavailable")

    monkeypatch.setattr(AgentClient, "execute", fake_execute)
    project = create_project(client, auth_headers)
    response = client.request(
        "DELETE",
        f"/api/v1/projects/{project['id']}",
        headers=auth_headers,
        json={"confirm_project_name": project["name"]},
    )
    assert response.status_code == 502
    assert response.json()["detail"] == "System agent could not clean up project resources"
    assert (
        client.get(f"/api/v1/projects/{project['id']}", headers=auth_headers).status_code == 200
    )


def test_sftp_status_returns_disabled_without_account(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    """Missing SFTP configuration is a disabled state, not a page-breaking error."""
    project = create_project(client, auth_headers)
    response = client.get(
        f"/api/v1/projects/{project['id']}/sftp",
        headers=auth_headers,
    )
    assert response.status_code == 200
    assert response.json() == {
        "enabled": False,
        "username": None,
        "directory": "/uploads",
        "one_time_password": None,
    }


def test_create_project_from_template_materializes_files(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    """Built-in templates create project metadata and starter files."""
    templates = client.get("/api/v1/projects/templates", headers=auth_headers)
    assert templates.status_code == 200
    assert any(item["id"] == "fastapi" for item in templates.json())
    created = client.post(
        "/api/v1/projects/from-template",
        headers=auth_headers,
        json={"template_id": "fastapi", "name": "Template API"},
    )
    assert created.status_code == 201
    payload = created.json()
    assert payload["project_type"] == "backend"
    assert payload["runtime_type"] == "docker"
    assert payload["install_command"] is None
    assert payload["build_command"] is None
    assert payload["start_command"].startswith("uvicorn ")
    assert payload["runtime_config"]["internal_port"] == 8000
    root = Path("/tmp/nectarine-panel-tests/projects") / payload["id"]
    assert (root / "Dockerfile").is_file()
    assert (root / "requirements.txt").read_text(encoding="utf-8").startswith("fastapi")


def test_builtin_templates_cover_required_project_types(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    """Every required built-in template exposes starter files."""
    from app.services.templates import TEMPLATES

    expected = {
        "fastapi",
        "nuxt",
        "vue-vite",
        "react-vite",
        "next",
        "node-api",
        "telegram-bot-python",
        "discord-bot-python",
        "static-site",
        "docker-compose",
        "minecraft-forge",
    }
    response = client.get("/api/v1/projects/templates", headers=auth_headers)
    assert response.status_code == 200
    assert {item["id"] for item in response.json()} == expected
    assert all(TEMPLATES[template_id].files for template_id in expected)
    docker_templates = [
        template for template in TEMPLATES.values() if template.runtime_type == "docker"
    ]
    assert all(template.install_command is None for template in docker_templates)
    assert all(template.build_command is None for template in docker_templates)
    assert all(template.runtime_config.get("internal_port") for template in docker_templates)
    assert TEMPLATES["docker-compose"].start_command is None
    expected_bot_mount = [{"source": "data", "target": "/app/data", "read_only": False}]
    assert (
        TEMPLATES["telegram-bot-python"].runtime_config["persistent_mounts"]
        == expected_bot_mount
    )
    assert (
        TEMPLATES["discord-bot-python"].runtime_config["persistent_mounts"]
        == expected_bot_mount
    )


def test_github_webhook_token_triggers_deploy(
    client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stored webhook token queues a Git deployment without exposing secrets."""
    from app.api.routes import jobs

    queued: list[tuple[str, str, dict[str, object]]] = []
    monkeypatch.setattr(
        jobs,
        "enqueue",
        lambda name, task_id, kwargs: queued.append((name, task_id, kwargs)),
    )
    project = create_project(client, auth_headers)
    created = client.post(
        f"/api/v1/projects/{project['id']}/webhook",
        headers=auth_headers,
    )
    assert created.status_code == 201
    webhook = created.json()
    assert webhook["token"] in webhook["url"]
    body = b'{"ref":"refs/heads/main"}'
    signature = (
        "sha256="
        + hmac.new(
            webhook["hmac_secret"].encode("utf-8"),
            body,
            hashlib.sha256,
        ).hexdigest()
    )
    missing_signature = client.post(f"/api/v1/webhooks/github/{webhook['token']}")
    assert missing_signature.status_code == 401
    triggered = client.post(
        f"/api/v1/webhooks/github/{webhook['token']}",
        content=body,
        headers={"X-Hub-Signature-256": signature},
    )
    assert triggered.status_code == 200
    job_id = triggered.json()["id"]
    assert queued[0][0] == "projects.git_deploy"
    assert queued[0][1] == job_id
    assert queued[0][2]["project_id"] == project["id"]
    assert webhook["token"] not in triggered.text
    assert webhook["hmac_secret"] not in triggered.text


def test_project_git_credential_is_masked(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    """Private Git credentials are accepted but never returned in plaintext."""
    project = create_project(client, auth_headers)
    empty = client.get(
        f"/api/v1/projects/{project['id']}/source/credential",
        headers=auth_headers,
    )
    assert empty.status_code == 200
    assert empty.json() == {
        "configured": False,
        "credential_type": None,
        "username": None,
    }
    response = client.put(
        f"/api/v1/projects/{project['id']}/source/credential",
        headers=auth_headers,
        json={
            "credential_type": "token",
            "username": "octocat",
            "value": "github_pat_secret_value",
        },
    )
    assert response.status_code == 200
    assert response.json() == {
        "configured": True,
        "credential_type": "token",
        "username": "octocat",
    }
    assert "github_pat_secret_value" not in response.text
    status = client.get(
        f"/api/v1/projects/{project['id']}/source/credential",
        headers=auth_headers,
    )
    assert status.status_code == 200
    assert status.json() == {
        "configured": True,
        "credential_type": "token",
        "username": "octocat",
    }
    assert "github_pat_secret_value" not in status.text
    deleted = client.delete(
        f"/api/v1/projects/{project['id']}/source/credential",
        headers=auth_headers,
    )
    assert deleted.status_code == 200
    assert deleted.json() == {
        "configured": False,
        "credential_type": None,
        "username": None,
    }


def test_backup_policy_persists_items_and_database_selection(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    """Backup policy API persists explicit file groups and database selection."""
    project = create_project(client, auth_headers)
    response = client.put(
        f"/api/v1/projects/{project['id']}/backup-policy",
        headers=auth_headers,
        json={
            "enabled": True,
            "schedule": "0 4 * * *",
            "retention_count": 5,
            "include_items": [".env", "uploads"],
            "include_databases": False,
            "encryption_enabled": False,
        },
    )
    assert response.status_code == 200
    assert response.json()["include_items"] == [".env", "uploads"]
    assert response.json()["include_databases"] is False
    fetched = client.get(
        f"/api/v1/projects/{project['id']}/backup-policy",
        headers=auth_headers,
    )
    assert fetched.status_code == 200
    assert fetched.json()["include_items"] == [".env", "uploads"]
    assert fetched.json()["include_databases"] is False


def test_secret_environment_values_are_masked(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """Environment secret values never return through list endpoints."""
    project = create_project(client, auth_headers)
    project_id = project["id"]
    created = client.put(
        f"/api/v1/projects/{project_id}/env/DATABASE_URL",
        headers=auth_headers,
        json={
            "key": "DATABASE_URL",
            "value": "postgresql://secret",
            "is_secret": True,
        },
    )
    assert created.status_code == 200
    assert created.json()["value"] == "••••••••"
    listed = client.get(f"/api/v1/projects/{project_id}/env", headers=auth_headers)
    assert listed.json()[0]["value"] == "••••••••"
    assert "postgresql://secret" not in listed.text


def test_environment_reveal_requires_exact_confirmation_and_audits_safely(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    """Secret reveal returns plaintext once and never stores it in audit payloads."""
    project = create_project(client, auth_headers)
    project_id = project["id"]
    client.put(
        f"/api/v1/projects/{project_id}/env/API_TOKEN",
        headers=auth_headers,
        json={"key": "API_TOKEN", "value": "super-secret-token", "is_secret": True},
    )
    rejected = client.post(
        f"/api/v1/projects/{project_id}/env/API_TOKEN/reveal",
        headers=auth_headers,
        json={"confirm_key": "DATABASE_URL"},
    )
    assert rejected.status_code == 409
    revealed = client.post(
        f"/api/v1/projects/{project_id}/env/API_TOKEN/reveal",
        headers=auth_headers,
        json={"confirm_key": "API_TOKEN", "reason": "incident check"},
    )
    assert revealed.status_code == 200
    assert revealed.json() == {
        "key": "API_TOKEN",
        "value": "super-secret-token",
        "is_secret": True,
    }
    audit = client.get("/api/v1/audit-logs", headers=auth_headers)
    assert audit.status_code == 200
    assert "project.env_reveal" in audit.text
    assert "super-secret-token" not in audit.text


def test_environment_versions_and_rollback_restore_previous_value(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    """Environment updates create history rows and rollback creates a new safe version."""
    project = create_project(client, auth_headers)
    project_id = project["id"]
    first = client.put(
        f"/api/v1/projects/{project_id}/env/FEATURE_FLAG",
        headers=auth_headers,
        json={"key": "FEATURE_FLAG", "value": "off", "is_secret": False},
    )
    assert first.status_code == 200
    second = client.put(
        f"/api/v1/projects/{project_id}/env/FEATURE_FLAG",
        headers=auth_headers,
        json={"key": "FEATURE_FLAG", "value": "on", "is_secret": False},
    )
    assert second.status_code == 200
    versions = client.get(
        f"/api/v1/projects/{project_id}/env/FEATURE_FLAG/versions",
        headers=auth_headers,
    )
    assert versions.status_code == 200
    payload = versions.json()
    assert [item["action"] for item in payload] == ["update", "create"]
    create_version = next(item for item in payload if item["action"] == "create")
    assert create_version["value_sha256"] is not None
    assert "off" not in versions.text
    rolled_back = client.post(
        f"/api/v1/projects/{project_id}/env/FEATURE_FLAG/rollback",
        headers=auth_headers,
        json={
            "version_id": create_version["id"],
            "confirm_key": "FEATURE_FLAG",
            "reason": "bad release",
        },
    )
    assert rolled_back.status_code == 200
    assert rolled_back.json()["value"] == "off"
    after = client.get(
        f"/api/v1/projects/{project_id}/env/FEATURE_FLAG/versions",
        headers=auth_headers,
    )
    assert after.json()[0]["action"] == "rollback"


def test_environment_delete_creates_tombstone_version(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    """Deleting a variable preserves a non-restorable tombstone history row."""
    project = create_project(client, auth_headers)
    project_id = project["id"]
    client.put(
        f"/api/v1/projects/{project_id}/env/REMOVED_KEY",
        headers=auth_headers,
        json={"key": "REMOVED_KEY", "value": "old-value", "is_secret": True},
    )
    deleted = client.delete(
        f"/api/v1/projects/{project_id}/env/REMOVED_KEY",
        headers=auth_headers,
    )
    assert deleted.status_code == 200
    versions = client.get(
        f"/api/v1/projects/{project_id}/env/REMOVED_KEY/versions",
        headers=auth_headers,
    )
    assert versions.status_code == 200
    assert versions.json()[0]["action"] == "delete"
    restored = client.post(
        f"/api/v1/projects/{project_id}/env/REMOVED_KEY/rollback",
        headers=auth_headers,
        json={
            "version_id": versions.json()[0]["id"],
            "confirm_key": "REMOVED_KEY",
        },
    )
    assert restored.status_code == 409


def test_environment_import_validates_dotenv_and_replace_mode(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    """Dotenv import validates keys and can replace variables absent from payload."""
    project = create_project(client, auth_headers)
    project_id = project["id"]
    client.put(
        f"/api/v1/projects/{project_id}/env/OLD_KEY",
        headers=auth_headers,
        json={"key": "OLD_KEY", "value": "remove-me", "is_secret": True},
    )
    malformed = client.post(
        f"/api/v1/projects/{project_id}/env/import",
        headers=auth_headers,
        json={"content": "1BAD=value", "mode": "merge"},
    )
    assert malformed.status_code == 422
    imported = client.post(
        f"/api/v1/projects/{project_id}/env/import",
        headers=auth_headers,
        json={
            "content": "DATABASE_URL=postgresql://db\nexport API_TOKEN='token-value'",
            "mode": "replace",
            "is_secret": True,
        },
    )
    assert imported.status_code == 200
    assert imported.json() == {
        "created": 2,
        "updated": 0,
        "deleted": 1,
        "keys": ["API_TOKEN", "DATABASE_URL"],
    }
    listed = client.get(f"/api/v1/projects/{project_id}/env", headers=auth_headers)
    assert [item["key"] for item in listed.json()] == ["API_TOKEN", "DATABASE_URL"]
    assert "token-value" not in listed.text


def test_environment_secret_manager_endpoints_require_auth(client: TestClient) -> None:
    """Secrets Manager endpoints reject unauthenticated requests."""
    project_id = "00000000-0000-0000-0000-000000000000"
    responses = [
        client.get(f"/api/v1/projects/{project_id}/env/API_TOKEN/versions"),
        client.post(
            f"/api/v1/projects/{project_id}/env/API_TOKEN/reveal",
            json={"confirm_key": "API_TOKEN"},
        ),
        client.post(
            f"/api/v1/projects/{project_id}/env/API_TOKEN/rollback",
            json={
                "version_id": "11111111-1111-1111-1111-111111111111",
                "confirm_key": "API_TOKEN",
            },
        ),
        client.post(
            f"/api/v1/projects/{project_id}/env/import",
            json={"content": "API_TOKEN=value"},
        ),
    ]
    assert {response.status_code for response in responses} == {401}
