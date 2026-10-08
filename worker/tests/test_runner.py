"""Worker archive safety and backup manifest tests."""

import base64
import json
import sqlite3
import tarfile
import zipfile
from pathlib import Path

import httpx
import pytest
from nectarine_worker import tasks
from nectarine_worker.runner import (
    RunnerError,
    create_backup,
    create_tar_gz_safely,
    extract_tar_safely,
    extract_zip_safely,
    restore_backup,
    run_project_command,
)


class FakeTask:
    """Minimal Celery task double for runtime deployment tests."""

    def __init__(self) -> None:
        """Initialize captured progress events."""
        self.states: list[dict[str, object]] = []

    def update_state(self, *, state: str, meta: dict[str, object]) -> None:
        """Capture task progress."""
        self.states.append({"state": state, "meta": meta})


def test_extract_zip_rejects_traversal(tmp_path: Path) -> None:
    """ZIP entries cannot write outside the destination."""
    archive = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(archive, "w") as handle:
        handle.writestr("../escape.txt", "unsafe")
    with pytest.raises(RunnerError):
        extract_zip_safely(archive, tmp_path / "destination")


def test_backup_manifest_has_checksum(tmp_path: Path) -> None:
    """Backup creation emits a verifiable manifest."""
    source = tmp_path / "source"
    source.mkdir()
    (source / "data.txt").write_text("content", encoding="utf-8")
    target = tmp_path / "backups" / "project.tar.gz"
    manifest = create_backup(
        source,
        target,
        backup_type="project",
        project_id="project-id",
    )
    assert manifest["checksum"]
    assert manifest["size_bytes"] == target.stat().st_size


def test_env_backup_item_maps_to_runtime_env(tmp_path: Path) -> None:
    """Logical .env backup item stores the generated runtime environment file."""
    source = tmp_path / "project"
    (source / "shared").mkdir(parents=True)
    (source / "shared" / "runtime.env").write_text("SECRET=value", encoding="utf-8")
    (source / "current").mkdir()
    (source / "current" / "app.py").write_text("print('skip')", encoding="utf-8")
    target = tmp_path / "backup.tar.gz"
    manifest = create_backup(
        source,
        target,
        backup_type="project",
        project_id="project-id",
        include_items=[".env"],
    )
    with tarfile.open(target, "r:gz") as archive:
        names = archive.getnames()
    assert manifest["included_items"] == [".env"]
    assert f"{source.name}/shared/runtime.env" in names
    assert f"{source.name}/current/app.py" not in names


def test_extract_tar_rejects_symlink(tmp_path: Path) -> None:
    """TAR restore rejects links even when their target appears local."""
    archive = tmp_path / "unsafe.tar.gz"
    info = tarfile.TarInfo("root/link")
    info.type = tarfile.SYMTYPE
    info.linkname = "/etc/passwd"
    with tarfile.open(archive, "w:gz") as handle:
        handle.addfile(info)
    with pytest.raises(RunnerError):
        extract_tar_safely(archive, tmp_path / "destination")


def test_tar_gz_file_manager_archive_round_trip(tmp_path: Path) -> None:
    """Safe TAR.GZ creation and extraction preserve regular project files."""
    root = tmp_path / "project"
    source = root / "data"
    source.mkdir(parents=True)
    (source / "value.txt").write_text("content", encoding="utf-8")
    archive = root / "data.tar.gz"
    create_tar_gz_safely(source, archive, root)
    destination = root / "extracted"
    extract_tar_safely(archive, destination)
    assert (destination / "data" / "value.txt").read_text(encoding="utf-8") == "content"


def test_tar_gz_archive_rejects_destination_inside_source(tmp_path: Path) -> None:
    """Archive creation cannot recursively include its own output."""
    root = tmp_path / "project"
    source = root / "data"
    source.mkdir(parents=True)
    with pytest.raises(RunnerError, match="inside its source"):
        create_tar_gz_safely(source, source / "archive.tar.gz", root)


@pytest.mark.parametrize("managed", [False, True])
def test_file_archive_uses_agent_for_project_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    managed: bool,
) -> None:
    """Protected projects inside and outside storage delegate archive creation."""
    external_root = (
        tasks.settings.storage_root / "projects" / "protected-project" / "current"
        if managed
        else tmp_path / "external-project"
    )
    calls: list[tuple[str, dict[str, object], float]] = []

    def fake_agent_operation(
        operation: str,
        parameters: dict[str, object] | None = None,
        *,
        timeout: float = 30,
    ) -> dict[str, object]:
        """Capture the privileged archive request."""
        calls.append((operation, parameters or {}, timeout))
        return {"path": "src.zip", "size_bytes": 42}

    monkeypatch.setattr(tasks, "agent_operation", fake_agent_operation)
    monkeypatch.setattr(tasks.archive_files, "update_state", lambda **kwargs: None)

    result = tasks.archive_files.run(
        project_root=str(external_root),
        source="src",
        destination="src.zip",
    )

    assert result == {"path": "src.zip", "size_bytes": 42}
    assert calls == [
        (
            "archive_path",
            {
                "root": str(external_root),
                "source": "src",
                "destination": "src.zip",
            },
            3600,
        )
    ]


def test_restore_backup_replaces_destination(tmp_path: Path) -> None:
    """A valid backup replaces an existing destination after checksum validation."""
    source = tmp_path / "project"
    source.mkdir()
    (source / "value.txt").write_text("new", encoding="utf-8")
    archive = tmp_path / "backup.tar.gz"
    create_backup(
        source,
        archive,
        backup_type="project",
        project_id="project-id",
    )
    (source / "value.txt").write_text("old", encoding="utf-8")
    restore_backup(
        archive,
        source,
        archive.with_suffix(archive.suffix + ".manifest.json"),
    )
    assert (source / "value.txt").read_text(encoding="utf-8") == "new"


def test_restore_backup_rejects_wrong_project_id(tmp_path: Path) -> None:
    """Project restore refuses artifacts created for another project."""
    source = tmp_path / "project"
    source.mkdir()
    (source / "value.txt").write_text("new", encoding="utf-8")
    archive = tmp_path / "backup.tar.gz"
    create_backup(
        source,
        archive,
        backup_type="project",
        project_id="project-id",
    )
    with pytest.raises(RunnerError, match="another project"):
        restore_backup(
            archive,
            source,
            archive.with_suffix(archive.suffix + ".manifest.json"),
            expected_project_id="other-project-id",
        )


def test_restore_backup_rejects_incompatible_manifest(tmp_path: Path) -> None:
    """Project restore refuses artifacts outside the declared compatibility range."""
    source = tmp_path / "project"
    source.mkdir()
    (source / "value.txt").write_text("new", encoding="utf-8")
    archive = tmp_path / "backup.tar.gz"
    create_backup(
        source,
        archive,
        backup_type="project",
        project_id="project-id",
    )
    manifest_path = archive.with_suffix(archive.suffix + ".manifest.json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["restore_compatibility"] = ">=9.0.0,<10.0.0"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(RunnerError, match="not compatible"):
        restore_backup(archive, source, manifest_path)


def test_encrypted_backup_round_trip(tmp_path: Path) -> None:
    """Encrypted backups authenticate and restore without plaintext artifacts."""
    source = tmp_path / "project"
    source.mkdir()
    (source / ".env").write_text("SECRET=value", encoding="utf-8")
    target = tmp_path / "backup.tar.gz"
    key = base64.urlsafe_b64encode(b"k" * 32).decode("ascii")
    manifest = create_backup(
        source,
        target,
        backup_type="project",
        project_id="project-id",
        encryption_key=key,
    )
    encrypted = Path(manifest["artifact_path"])
    assert encrypted.suffix == ".enc"
    assert not target.exists()
    (source / ".env").write_text("changed", encoding="utf-8")
    restore_backup(
        encrypted,
        source,
        encrypted.with_suffix(encrypted.suffix + ".manifest.json"),
        encryption_key=key,
    )
    assert (source / ".env").read_text(encoding="utf-8") == "SECRET=value"


def test_full_panel_backup_contains_configs_storage_and_panel_db(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Full-panel backup archives selected panel state without backup recursion."""
    storage = tmp_path / "storage"
    config = tmp_path / "config"
    nginx = tmp_path / "nginx"
    database = tmp_path / "panel.db"
    (storage / "projects" / "project-id").mkdir(parents=True)
    (storage / "projects" / "project-id" / "app.txt").write_text("app", encoding="utf-8")
    (storage / "backups" / "full").mkdir(parents=True)
    (storage / "backups" / "full" / "old.tar.gz").write_text("skip", encoding="utf-8")
    config.mkdir()
    (config / "panel.env").write_text("SECRET=value", encoding="utf-8")
    nginx.mkdir()
    (nginx / "site.conf").write_text("server {}", encoding="utf-8")
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE sample (value TEXT)")
        connection.commit()

    async def mark_ready(*args: object, **kwargs: object) -> None:
        """Skip database persistence for this unit test."""
        del args, kwargs

    async def mark_failed(*args: object, **kwargs: object) -> None:
        """Fail fast if the backup task records an error."""
        del args, kwargs
        raise AssertionError("full backup failed")

    monkeypatch.setattr(tasks.settings, "storage_root", storage)
    monkeypatch.setattr(tasks.settings, "config_root", config)
    monkeypatch.setattr(tasks.settings, "nginx_config_root", nginx)
    monkeypatch.setattr(tasks.settings, "database_url", f"sqlite:///{database}")
    monkeypatch.setattr(tasks.backup_full_panel, "update_state", lambda **kwargs: None)
    monkeypatch.setattr(tasks, "mark_backup_ready", mark_ready)
    monkeypatch.setattr(tasks, "mark_backup_failed", mark_failed)

    result = tasks.backup_full_panel.run(
        backup_id="backup-id",
        backup_root=str(storage / "backups"),
        include_items=["panel_db", "panel_config", "nginx_configs", "project_files"],
    )

    archive = Path(str(result["path"]))
    assert result["type"] == "full"
    assert result["included_items"] == [
        "panel_db",
        "panel_config",
        "nginx_configs",
        "project_files",
    ]
    with tarfile.open(archive, "r:gz") as handle:
        names = set(handle.getnames())
    assert "nectarine-full/panel_db/panel.sqlite3" in names
    assert "nectarine-full/panel_config/panel.env" in names
    assert "nectarine-full/nginx_configs/site.conf" in names
    assert "nectarine-full/storage/projects/project-id/app.txt" in names
    assert "nectarine-full/storage/backups/full/old.tar.gz" not in names


def test_docker_runtime_materializes_env_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Docker deploy receives a generated env-file without leaking values in logs."""
    project_id = "11111111-1111-1111-1111-111111111111"
    root = tmp_path / "projects" / project_id
    release = root / "releases" / "release-1"
    release.mkdir(parents=True)
    captured: dict[str, object] = {}
    persisted: dict[str, object] = {}

    async def fake_environment(target_project_id: str) -> dict[str, str]:
        """Return deterministic environment variables."""
        assert target_project_id == project_id
        return {"DATABASE_URL": "postgresql://secret", "PORT": "8000"}

    async def fake_policy(target_project_id: str) -> dict[str, object]:
        """Return a disabled resource policy."""
        assert target_project_id == project_id
        return {"enabled": False}

    def fake_agent(
        operation: str,
        parameters: dict[str, object] | None = None,
        *,
        timeout: float = 30,
    ) -> dict[str, object]:
        """Capture the requested agent operation."""
        del timeout
        captured["operation"] = operation
        captured["parameters"] = parameters or {}
        return {"ok": True, "host_port": 18080}

    async def fake_persist(target_project_id: str, host_port: int) -> None:
        """Capture the canonical Docker port persistence request."""
        persisted["project_id"] = target_project_id
        persisted["host_port"] = host_port

    monkeypatch.setattr(tasks, "project_environment", fake_environment)
    monkeypatch.setattr(tasks, "project_resource_policy", fake_policy)
    monkeypatch.setattr(tasks, "agent_operation", fake_agent)
    monkeypatch.setattr(tasks, "_persist_docker_upstream_port", fake_persist)
    result = tasks._deploy_runtime(
        FakeTask(),
        project_id=project_id,
        release_id="release-1",
        root=root,
        release=release,
        runtime_type="docker",
        runtime_config={
            "internal_port": 8000,
            "upstream_port": 18080,
            "start_command": "python -m app",
            "persistent_mounts": [
                {"source": "data", "target": "/app/data", "read_only": False}
            ],
        },
    )
    assert result == {"ok": True, "host_port": 18080}
    assert captured["operation"] == "deploy_container"
    parameters = captured["parameters"]
    assert isinstance(parameters, dict)
    assert parameters["env_file"] == f"projects/{project_id}/shared/runtime.env"
    assert parameters["host_port"] == 18080
    assert parameters["start_command"] == "python -m app"
    assert parameters["persistent_mounts"] == [
        {"source": "data", "target": "/app/data", "read_only": False}
    ]
    assert persisted == {"project_id": project_id, "host_port": 18080}
    env_file = root / "shared" / "runtime.env"
    assert env_file.read_text(encoding="utf-8") == (
        "DATABASE_URL=postgresql://secret\nPORT=8000\n"
    )
    assert env_file.stat().st_mode & 0o777 == 0o600


def test_agent_operation_exposes_safe_agent_detail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Worker failures retain the agent reason without the generic HTTP help URL."""

    def fake_post(*args: object, **kwargs: object) -> httpx.Response:
        """Return a deterministic rejected agent response."""
        del args, kwargs
        return httpx.Response(
            422,
            json={"detail": "Docker host port 18000 is already in use"},
            request=httpx.Request("POST", "http://127.0.0.1:18090/v1/operations"),
        )

    monkeypatch.setattr(tasks.httpx, "post", fake_post)

    with pytest.raises(
        tasks.AgentOperationError,
        match="Docker host port 18000 is already in use",
    ):
        tasks.agent_operation("deploy_container")


def test_systemd_runtime_requires_start_command(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Systemd deployments fail before reaching the agent without a start command."""
    project_id = "11111111-1111-1111-1111-111111111111"
    root = tmp_path / "projects" / project_id
    release = root / "releases" / "release-1"
    release.mkdir(parents=True)

    async def fake_environment(target_project_id: str) -> dict[str, str]:
        """Return an empty project environment."""
        assert target_project_id == project_id
        return {}

    async def fake_policy(target_project_id: str) -> dict[str, object]:
        """Return a disabled resource policy."""
        assert target_project_id == project_id
        return {"enabled": False}

    monkeypatch.setattr(tasks, "project_environment", fake_environment)
    monkeypatch.setattr(tasks, "project_resource_policy", fake_policy)
    with pytest.raises(ValueError, match="requires a start command"):
        tasks._deploy_runtime(
            FakeTask(),
            project_id=project_id,
            release_id="release-1",
            root=root,
            release=release,
            runtime_type="systemd",
            runtime_config={},
        )


def test_pm2_runtime_dispatches_to_agent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PM2 deployments pass a bounded start command through the agent."""
    project_id = "11111111-1111-1111-1111-111111111111"
    root = tmp_path / "projects" / project_id
    release = root / "releases" / "release-1"
    release.mkdir(parents=True)
    captured: dict[str, object] = {}

    async def fake_environment(target_project_id: str) -> dict[str, str]:
        """Return deterministic environment variables."""
        assert target_project_id == project_id
        return {"NODE_ENV": "production"}

    async def fake_policy(target_project_id: str) -> dict[str, object]:
        """Return a disabled resource policy."""
        assert target_project_id == project_id
        return {"enabled": False}

    def fake_agent(
        operation: str,
        parameters: dict[str, object] | None = None,
        *,
        timeout: float = 30,
    ) -> dict[str, object]:
        """Capture the requested agent operation."""
        del timeout
        captured["operation"] = operation
        captured["parameters"] = parameters or {}
        return {"service": "vps-project"}

    monkeypatch.setattr(tasks, "project_environment", fake_environment)
    monkeypatch.setattr(tasks, "project_resource_policy", fake_policy)
    monkeypatch.setattr(tasks, "agent_operation", fake_agent)
    result = tasks._deploy_runtime(
        FakeTask(),
        project_id=project_id,
        release_id="release-1",
        root=root,
        release=release,
        runtime_type="pm2",
        runtime_config={"start_command": "npm run start"},
    )
    assert result == {"service": "vps-project"}
    assert captured["operation"] == "deploy_pm2"
    parameters = captured["parameters"]
    assert isinstance(parameters, dict)
    assert parameters["start_command"] == "npm run start"
    assert parameters["env_file"] == f"projects/{project_id}/shared/runtime.env"


def test_docker_runtime_passes_resource_policy_to_agent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Docker deployments pass enabled CPU and memory limits to the agent."""
    project_id = "11111111-1111-1111-1111-111111111111"
    root = tmp_path / "projects" / project_id
    release = root / "releases" / "release-1"
    release.mkdir(parents=True)
    captured: dict[str, object] = {}

    async def fake_environment(target_project_id: str) -> dict[str, str]:
        """Return an empty environment."""
        assert target_project_id == project_id
        return {}

    async def fake_policy(target_project_id: str) -> dict[str, object]:
        """Return an enabled resource policy."""
        assert target_project_id == project_id
        return {"enabled": True, "cpu_cores": 1.25, "memory_mb": 512}

    def fake_agent(
        operation: str,
        parameters: dict[str, object] | None = None,
        *,
        timeout: float = 30,
    ) -> dict[str, object]:
        """Capture the requested agent operation."""
        del operation, timeout
        captured.update(parameters or {})
        return {"ok": True}

    monkeypatch.setattr(tasks, "project_environment", fake_environment)
    monkeypatch.setattr(tasks, "project_resource_policy", fake_policy)
    monkeypatch.setattr(tasks, "agent_operation", fake_agent)

    tasks._deploy_runtime(
        FakeTask(),
        project_id=project_id,
        release_id="release-1",
        root=root,
        release=release,
        runtime_type="docker",
        runtime_config={"internal_port": 8000, "host_port": 18080},
    )

    assert captured["cpu_cores"] == 1.25
    assert captured["memory_mb"] == 512


def test_run_project_command_rejects_control_characters(tmp_path: Path) -> None:
    """Project commands cannot contain unit-breaking control characters."""
    with pytest.raises(RunnerError, match="control characters"):
        run_project_command("npm install\nnpm run build", tmp_path)


def test_git_token_credential_environment_uses_askpass(tmp_path: Path) -> None:
    """Git token credentials are exposed through temporary askpass only."""
    root = tmp_path / "projects" / "project-id"
    root.mkdir(parents=True)
    credential = {
        "type": "token",
        "username": "octocat",
        "value": "github_pat_secret_value",
    }
    with tasks._git_credential_environment("project-id", root, credential) as env:
        askpass = Path(env["GIT_ASKPASS"])
        assert askpass.is_file()
        assert env["GIT_USERNAME"] == "octocat"
        assert env["GIT_PASSWORD"] == "github_pat_secret_value"
        assert askpass.stat().st_mode & 0o777 == 0o700
    assert not askpass.exists()


def test_git_deploy_key_environment_uses_ssh_command(tmp_path: Path) -> None:
    """Git deploy key credentials are written to a temporary private key file."""
    root = tmp_path / "projects" / "project-id"
    root.mkdir(parents=True)
    credential = {
        "type": "deploy_key",
        "value": (
            "-----BEGIN OPENSSH PRIVATE KEY-----\nsecret\n-----END OPENSSH PRIVATE KEY-----\n"
        ),
        "username": "git",
    }
    with tasks._git_credential_environment("project-id", root, credential) as env:
        command = env["GIT_SSH_COMMAND"]
        key_path = Path(command.split(" -o ", 1)[0].removeprefix("ssh -i "))
        assert key_path.is_file()
        assert key_path.stat().st_mode & 0o777 == 0o600
    assert not key_path.exists()


def test_minecraft_backup_flushes_before_archive_and_resumes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Worker brackets a Minecraft archive with the agent consistency guard."""
    source = tmp_path / "minecraft"
    source.mkdir()
    events: list[str] = []

    async def fake_parameters(project_id: str) -> dict[str, object]:
        """Return deterministic local RCON parameters."""
        return {
            "project_id": project_id,
            "rcon_host_port": 25575,
            "rcon_password": "correct-rcon-password",
        }

    async def no_op(*args: object, **kwargs: object) -> None:
        """Replace backup database persistence for this unit test."""
        del args, kwargs

    def fake_agent(
        operation: str,
        parameters: dict[str, object] | None = None,
        *,
        timeout: float = 30,
    ) -> dict[str, object]:
        """Capture Minecraft backup guard transitions."""
        del timeout
        assert operation == "minecraft_backup"
        action = str((parameters or {})["action"])
        events.append(action)
        return {"prepared": action == "prepare"}

    def fake_create_backup(
        source_path: Path,
        target: Path,
        **kwargs: object,
    ) -> dict[str, object]:
        """Create a minimal artifact between guard transitions."""
        del source_path, kwargs
        events.append("archive")
        target.parent.mkdir(parents=True)
        target.write_bytes(b"backup")
        return {
            "artifact_path": str(target),
            "size_bytes": 6,
            "checksum": "checksum",
        }

    monkeypatch.setattr(tasks, "_minecraft_backup_parameters", fake_parameters)

    async def fake_configuration(project_id: str) -> dict[str, object]:
        """Provide launcher metadata without opening an unrelated database."""
        return {"server_jar": "forge-server.jar", "java_version": 21}

    monkeypatch.setattr(tasks, "minecraft_backup_configuration", fake_configuration)
    monkeypatch.setattr(tasks, "mark_backup_ready", no_op)
    monkeypatch.setattr(tasks, "mark_backup_failed", no_op)
    monkeypatch.setattr(tasks, "agent_operation", fake_agent)
    monkeypatch.setattr(tasks, "create_backup", fake_create_backup)
    monkeypatch.setattr(tasks.backup_project, "update_state", lambda **kwargs: None)
    tasks.backup_project.run(
        backup_id="backup-id",
        source=str(source),
        backup_root=str(tmp_path / "backups"),
        project_id="11111111-1111-1111-1111-111111111111",
    )
    assert events == ["prepare", "archive", "resume"]
