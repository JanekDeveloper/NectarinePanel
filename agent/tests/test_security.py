"""System agent security tests."""

from pathlib import Path

import pytest
from system_agent import operations
from system_agent.config import AgentSettings
from system_agent.main import _execute_operation
from system_agent.protocol import Operation, OperationRequest, OperationResponse
from system_agent.security import safe_child


def test_safe_child_accepts_nested_path(tmp_path: Path) -> None:
    """A normal relative path remains below the configured root."""
    assert safe_child(tmp_path, "projects/abc") == tmp_path / "projects" / "abc"


def test_privileged_directory_creation_is_project_scoped(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The helper cannot create arbitrary paths or unsafe directory modes."""
    project_id = "11111111-1111-1111-1111-111111111111"
    monkeypatch.setattr(operations.settings, "storage_root", tmp_path)
    result = operations.create_directory(f"projects/{project_id}", mode=0o750)
    assert Path(str(result["path"])).is_dir()
    with pytest.raises(ValueError, match="Only project directories"):
        operations.create_directory("backups/full")
    with pytest.raises(ValueError, match="Unsupported"):
        operations.create_directory(f"minecraft/{project_id}", mode=0o777)


def test_production_agent_requires_privileged_helper() -> None:
    """Production cannot run privileged operations inside the HTTP process."""
    with pytest.raises(ValueError, match="AGENT_PRIVILEGED_HELPER"):
        AgentSettings(
            environment="production",
            agent_token="a" * 32,
            privileged_helper=None,
        )


def test_production_agent_reads_privileged_helper_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Production agent accepts the installer-rendered helper environment key."""
    monkeypatch.setenv(
        "AGENT_PRIVILEGED_HELPER",
        "/usr/local/libexec/nectarine-agent-helper",
    )

    settings = AgentSettings(environment="production", agent_token="a" * 32)

    assert settings.privileged_helper == Path("/usr/local/libexec/nectarine-agent-helper")


@pytest.mark.asyncio
async def test_production_boundary_uses_exact_sudo_helper(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sensitive operation parameters travel through stdin, never sudo argv."""
    from system_agent import main

    captured: dict[str, object] = {}
    request = OperationRequest(
        operation=Operation.ENABLE_SFTP,
        parameters={
            "project_id": "11111111-1111-1111-1111-111111111111",
            "password": "secret-password-not-in-argv",
        },
    )

    async def fake_run_command(
        arguments: list[str],
        command_timeout: int = 60,
        *,
        stdin_data: bytes | None = None,
        output_limit: int = 32768,
    ) -> dict[str, object]:
        """Capture the fixed privilege-boundary invocation."""
        captured.update(
            arguments=arguments,
            command_timeout=command_timeout,
            stdin_data=stdin_data,
            output_limit=output_limit,
        )
        return {
            "exit_code": 0,
            "stdout": OperationResponse(
                operation=request.operation,
                result={"username": "vps_test"},
            ).model_dump_json(),
            "stderr": "",
        }

    monkeypatch.setattr(main.settings, "sudo_binary", Path("/usr/bin/sudo"))
    monkeypatch.setattr(
        main.settings,
        "privileged_helper",
        Path("/usr/local/libexec/nectarine-agent-helper"),
    )
    monkeypatch.setattr(main.operations, "run_command", fake_run_command)
    response = await _execute_operation(request)

    assert captured["arguments"] == [
        "/usr/bin/sudo",
        "--non-interactive",
        "/usr/local/libexec/nectarine-agent-helper",
    ]
    assert b"secret-password-not-in-argv" in bytes(captured["stdin_data"])
    assert "secret-password-not-in-argv" not in repr(captured["arguments"])
    assert response.result == {"username": "vps_test"}


@pytest.mark.parametrize("path", ["../etc/passwd", "projects/../../root", "/../../etc"])
def test_safe_child_rejects_traversal(tmp_path: Path, path: str) -> None:
    """Traversal attempts cannot escape the storage root."""
    with pytest.raises(ValueError):
        safe_child(tmp_path, path)


@pytest.mark.parametrize(
    ("hostname", "port"),
    [
        ("example.com; include /etc/passwd", 8080),
        ("example.com", 22),
        ("localhost", 8080),
    ],
)
def test_proxy_configuration_rejects_untrusted_fields(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    hostname: str,
    port: int,
) -> None:
    """Nginx generation rejects directive injection and privileged ports."""
    monkeypatch.setattr(operations.settings, "nginx_config_root", tmp_path)
    with pytest.raises(ValueError):
        operations.configure_proxy(hostname, port)


def test_proxy_configuration_is_generated_from_bounded_fields(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A valid proxy config contains only the validated host and local upstream."""
    monkeypatch.setattr(operations.settings, "nginx_config_root", tmp_path)
    result = operations.configure_proxy("app.example.com", 8080, ssl_enabled=True)
    content = Path(result["path"]).read_text(encoding="utf-8")
    assert "server_name app.example.com;" in content
    assert "proxy_pass http://127.0.0.1:8080;" in content
    assert "/etc/letsencrypt/live/app.example.com/fullchain.pem" in content


def test_static_site_configuration_uses_project_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Static Nginx generation serves files only from project storage."""
    static_root = tmp_path / "projects" / "project-id" / "current" / "dist"
    static_root.mkdir(parents=True)
    monkeypatch.setattr(operations.settings, "storage_root", tmp_path)
    monkeypatch.setattr(operations.settings, "nginx_config_root", tmp_path / "nginx")
    result = operations.configure_static_site(
        "static.example.com",
        "projects/project-id/current/dist",
        ssl_enabled=False,
    )
    content = Path(result["path"]).read_text(encoding="utf-8")
    assert "server_name static.example.com;" in content
    assert f"root {tmp_path / 'projects/project-id/current/dist'};" in content
    assert "try_files $uri $uri/ /index.html;" in content


@pytest.mark.asyncio
async def test_deploy_container_rejects_env_file_outside_project(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Docker deploy accepts env files only under the target project root."""
    project_id = "11111111-1111-1111-1111-111111111111"
    release = tmp_path / "projects" / project_id / "releases" / "release-1"
    release.mkdir(parents=True)
    (release / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
    outside = tmp_path / "other.env"
    outside.write_text("SECRET=value\n", encoding="utf-8")
    monkeypatch.setattr(operations.settings, "storage_root", tmp_path)
    with pytest.raises(ValueError, match="Invalid project environment file"):
        await operations.deploy_container(
            project_id,
            "release-1",
            internal_port=8000,
            host_port=18080,
            env_file="other.env",
        )


@pytest.mark.asyncio
async def test_deploy_container_applies_resource_limits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Docker deploy passes validated CPU and memory limits to docker run."""
    project_id = "11111111-1111-1111-1111-111111111111"
    release = tmp_path / "projects" / project_id / "releases" / "release-1"
    release.mkdir(parents=True)
    (release / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
    commands: list[list[str]] = []

    class MissingContainer:
        """Fake docker inspect process for a missing container."""

        async def wait(self) -> int:
            """Return a non-zero exit code."""
            return 1

    async def fake_subprocess_exec(*args: object, **kwargs: object) -> MissingContainer:
        """Pretend the old container does not exist."""
        del args, kwargs
        return MissingContainer()

    async def fake_run_command(
        arguments: list[str],
        command_timeout: int = 60,
    ) -> dict[str, object]:
        """Capture docker build and run calls."""
        del command_timeout
        commands.append(arguments)
        return {"exit_code": 0, "stdout": "container-id", "stderr": ""}

    monkeypatch.setattr(operations.settings, "storage_root", tmp_path)
    monkeypatch.setattr(operations.asyncio, "create_subprocess_exec", fake_subprocess_exec)
    monkeypatch.setattr(operations, "run_command", fake_run_command)
    monkeypatch.setattr(operations, "_loopback_port_available", lambda port: port == 18080)

    result = await operations.deploy_container(
        project_id,
        "release-1",
        internal_port=8000,
        host_port=18080,
        start_command="python -m app",
        persistent_mounts=[{"source": "data", "target": "/app/data", "read_only": False}],
        cpu_cores=1.5,
        memory_mb=512,
    )

    docker_run = commands[-1]
    assert "--cpus" in docker_run
    assert docker_run[docker_run.index("--cpus") + 1] == "1.5"
    assert "--memory" in docker_run
    assert docker_run[docker_run.index("--memory") + 1] == "512m"
    mount = docker_run[docker_run.index("--mount") + 1]
    assert mount == (
        f"type=bind,source={tmp_path}/projects/{project_id}/shared/data,target=/app/data"
    )
    assert docker_run[docker_run.index("--group-add") + 1] == str(
        (tmp_path / "projects" / project_id).stat().st_gid
    )
    assert docker_run[-4:] == [
        "vps-project-11111111-1111-1111-1111-111111111111:release-1",
        "/bin/sh",
        "-lc",
        "python -m app",
    ]
    assert result["host_port"] == 18080
    assert result["command_overridden"] is True
    assert result["persistent_mounts"] == [
        {"source": "data", "target": "/app/data", "read_only": False}
    ]
    mount_path = tmp_path / "projects" / project_id / "shared" / "data"
    assert mount_path.is_dir()
    assert mount_path.stat().st_mode & 0o7777 == 0o2770


@pytest.mark.asyncio
async def test_deploy_container_preserves_existing_container_data(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The first persistent deploy imports data from the old container."""
    project_id = "11111111-1111-1111-1111-111111111111"
    release = tmp_path / "projects" / project_id / "releases" / "release-1"
    release.mkdir(parents=True)
    (release / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
    commands: list[list[str]] = []

    class ExistingContainer:
        """Fake docker inspect process for an existing container."""

        async def wait(self) -> int:
            """Return a successful exit code."""
            return 0

    async def fake_subprocess_exec(*args: object, **kwargs: object) -> ExistingContainer:
        """Pretend the old container exists."""
        del args, kwargs
        return ExistingContainer()

    async def fake_run_command(
        arguments: list[str],
        command_timeout: int = 60,
    ) -> dict[str, object]:
        """Capture Docker commands and emulate docker cp."""
        del command_timeout
        commands.append(arguments)
        if arguments[1:3] == ["container", "cp"]:
            destination = Path(arguments[-1])
            (destination / "kodane.db").write_bytes(b"sqlite-data")
        return {"exit_code": 0, "stdout": "container-id", "stderr": ""}

    monkeypatch.setattr(operations.settings, "storage_root", tmp_path)
    monkeypatch.setattr(operations.asyncio, "create_subprocess_exec", fake_subprocess_exec)
    monkeypatch.setattr(operations, "run_command", fake_run_command)
    monkeypatch.setattr(operations, "_loopback_port_available", lambda port: port == 18080)

    result = await operations.deploy_container(
        project_id,
        "release-1",
        internal_port=8000,
        host_port=18080,
        persistent_mounts=[{"source": "data", "target": "/app/data"}],
    )

    persisted = tmp_path / "projects" / project_id / "shared" / "data" / "kodane.db"
    assert persisted.read_bytes() == b"sqlite-data"
    assert persisted.stat().st_mode & 0o007 == 0
    assert result["migrated_mounts"] == [{"source": "data", "target": "/app/data"}]
    operations_in_order = [arguments[1:3] for arguments in commands]
    assert operations_in_order.index(["container", "stop"]) < operations_in_order.index(
        ["container", "cp"]
    )
    assert operations_in_order.index(["container", "cp"]) < operations_in_order.index(
        ["rm", "--force"]
    )


def test_docker_persistent_mounts_reject_unsafe_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Persistent Docker mounts stay under project shared storage."""
    project_id = "11111111-1111-1111-1111-111111111111"
    (tmp_path / "projects" / project_id).mkdir(parents=True)
    monkeypatch.setattr(operations.settings, "storage_root", tmp_path)

    with pytest.raises(ValueError, match="Invalid persistent mount"):
        operations._docker_persistent_mount_arguments(
            project_id,
            [{"source": "../escape", "target": "/app/data"}],
        )
    with pytest.raises(ValueError, match="Invalid persistent mount"):
        operations._docker_persistent_mount_arguments(
            project_id,
            [{"source": "data", "target": "app/data"}],
        )
    with pytest.raises(ValueError, match="targets must be unique"):
        operations._docker_persistent_mount_arguments(
            project_id,
            [
                {"source": "data", "target": "/app/data"},
                {"source": "uploads", "target": "/app/data"},
            ],
        )


@pytest.mark.asyncio
async def test_deploy_container_uses_dynamic_loopback_port(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Docker deploy persists the loopback port assigned by the engine."""
    project_id = "11111111-1111-1111-1111-111111111111"
    release = tmp_path / "projects" / project_id / "releases" / "release-1"
    release.mkdir(parents=True)
    (release / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
    commands: list[list[str]] = []

    class MissingContainer:
        """Fake docker inspect process for a missing container."""

        async def wait(self) -> int:
            """Return a non-zero exit code."""
            return 1

    async def fake_subprocess_exec(*args: object, **kwargs: object) -> MissingContainer:
        """Pretend the old container does not exist."""
        del args, kwargs
        return MissingContainer()

    async def fake_run_command(
        arguments: list[str],
        command_timeout: int = 60,
    ) -> dict[str, object]:
        """Capture Docker calls and return one assigned host port."""
        del command_timeout
        commands.append(arguments)
        stdout = (
            "127.0.0.1:32768\n" if arguments[1:3] == ["container", "port"] else "container-id"
        )
        return {"exit_code": 0, "stdout": stdout, "stderr": ""}

    monkeypatch.setattr(operations.settings, "storage_root", tmp_path)
    monkeypatch.setattr(operations.asyncio, "create_subprocess_exec", fake_subprocess_exec)
    monkeypatch.setattr(operations, "run_command", fake_run_command)

    result = await operations.deploy_container(
        project_id,
        "release-1",
        internal_port=8000,
    )

    docker_run = commands[1]
    assert docker_run[docker_run.index("--publish") + 1] == "127.0.0.1::8000"
    assert commands[2][-1] == "8000/tcp"
    assert result["host_port"] == 32768
    assert result["command_overridden"] is False


@pytest.mark.asyncio
async def test_deploy_systemd_writes_restricted_unit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Systemd runtime writes a bounded unit for the requested project release."""
    project_id = "11111111-1111-1111-1111-111111111111"
    release = tmp_path / "projects" / project_id / "releases" / "release-1"
    release.mkdir(parents=True)
    env_file = tmp_path / "projects" / project_id / "shared" / "runtime.env"
    env_file.parent.mkdir(parents=True)
    env_file.write_text("PORT=3000\nSECRET=value\nPERCENT=100%\n", encoding="utf-8")
    commands: list[list[str]] = []

    async def fake_run_command(
        arguments: list[str],
        command_timeout: int = 60,
    ) -> dict[str, object]:
        """Capture systemctl calls without touching the host."""
        del command_timeout
        commands.append(arguments)
        return {"exit_code": 0, "stdout": "", "stderr": ""}

    monkeypatch.setattr(operations.settings, "storage_root", tmp_path)
    monkeypatch.setattr(operations.settings, "systemd_unit_root", tmp_path / "systemd")
    monkeypatch.setattr(operations.shutil, "chown", lambda *args, **kwargs: None)
    monkeypatch.setattr(operations, "run_command", fake_run_command)
    result = await operations.deploy_systemd(
        project_id,
        "release-1",
        start_command="node server.js",
        env_file=f"projects/{project_id}/shared/runtime.env",
    )
    unit = Path(str(result["unit_path"]))
    content = unit.read_text(encoding="utf-8")
    runtime_user = operations._project_runtime_username(project_id)
    assert f"WorkingDirectory={release}" in content
    assert f"User={runtime_user}" in content
    assert "Group=vps-panel" in content
    assert "UMask=0027" in content
    assert 'Environment="PORT=3000"' in content
    assert 'Environment="PERCENT=100%%"' in content
    assert 'ExecStart=/bin/sh -lc "node server.js"' in content
    assert "NoNewPrivileges=true" in content
    assert any(command[-1] == runtime_user for command in commands)
    assert commands[-1][-2:] == ["restart", f"vps-project-{project_id}.service"]


@pytest.mark.asyncio
async def test_deploy_systemd_writes_resource_limits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Systemd runtime persists CPUQuota and MemoryMax in the generated unit."""
    project_id = "11111111-1111-1111-1111-111111111111"
    release = tmp_path / "projects" / project_id / "releases" / "release-1"
    release.mkdir(parents=True)

    async def fake_run_command(
        arguments: list[str],
        command_timeout: int = 60,
    ) -> dict[str, object]:
        """Accept systemctl calls without touching the host."""
        del arguments, command_timeout
        return {"exit_code": 0, "stdout": "", "stderr": ""}

    monkeypatch.setattr(operations.settings, "storage_root", tmp_path)
    monkeypatch.setattr(operations.settings, "systemd_unit_root", tmp_path / "systemd")
    monkeypatch.setattr(operations.shutil, "chown", lambda *args, **kwargs: None)
    monkeypatch.setattr(operations, "run_command", fake_run_command)

    result = await operations.deploy_systemd(
        project_id,
        "release-1",
        start_command="node server.js",
        cpu_cores=2.5,
        memory_mb=768,
    )

    content = Path(str(result["unit_path"])).read_text(encoding="utf-8")
    assert "CPUQuota=250.0%" in content
    assert "MemoryMax=768M" in content


@pytest.mark.asyncio
async def test_deploy_systemd_rejects_multiline_start_command(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Systemd runtime rejects commands that can corrupt generated unit files."""
    project_id = "11111111-1111-1111-1111-111111111111"
    release = tmp_path / "projects" / project_id / "releases" / "release-1"
    release.mkdir(parents=True)
    monkeypatch.setattr(operations.settings, "storage_root", tmp_path)
    with pytest.raises(ValueError, match="Invalid runtime start command"):
        await operations.deploy_systemd(
            project_id,
            "release-1",
            start_command="npm start\nrm -rf /",
        )


@pytest.mark.asyncio
async def test_deploy_pm2_uses_foreground_runtime_and_writable_home(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PM2 runs in the service foreground with project-scoped writable state."""
    project_id = "11111111-1111-1111-1111-111111111111"
    release = tmp_path / "projects" / project_id / "releases" / "release-1"
    release.mkdir(parents=True)

    async def fake_run_command(
        arguments: list[str],
        command_timeout: int = 60,
    ) -> dict[str, object]:
        """Accept systemctl calls without touching the host."""
        del arguments, command_timeout
        return {"exit_code": 0, "stdout": "", "stderr": ""}

    monkeypatch.setattr(operations.settings, "storage_root", tmp_path)
    monkeypatch.setattr(operations.settings, "systemd_unit_root", tmp_path / "systemd")
    monkeypatch.setattr(operations.settings, "pm2_runtime_binary", Path("/usr/bin/pm2-runtime"))
    monkeypatch.setattr(operations.shutil, "chown", lambda *args, **kwargs: None)
    monkeypatch.setattr(operations, "run_command", fake_run_command)
    result = await operations.deploy_pm2(
        project_id,
        "release-1",
        start_command="node -e \"console.log('ready')\"",
    )
    content = Path(str(result["unit_path"])).read_text(encoding="utf-8")
    expected_home = tmp_path / "projects" / project_id / "logs" / "pm2"
    runtime_user = operations._project_runtime_username(project_id)
    assert f"User={runtime_user}" in content
    assert f'Environment="PM2_HOME={expected_home}"' in content
    assert "exec /usr/bin/pm2-runtime /bin/sh" in content
    assert "--name vps-project-11111111-1111-1111-1111-111111111111" in content


@pytest.mark.asyncio
async def test_install_minecraft_detects_modern_forge_launcher(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Forge installation runs in a restricted JDK container and detects run.sh."""
    project_id = "11111111-1111-1111-1111-111111111111"
    root = tmp_path / "minecraft" / project_id
    root.mkdir(parents=True)
    installer = "forge-1.20.1-installer.jar"
    (root / installer).write_bytes(b"installer")
    captured: list[str] = []

    async def fake_run_command(
        arguments: list[str],
        command_timeout: int = 60,
    ) -> dict[str, object]:
        """Capture Docker argv and emulate a modern Forge installation."""
        assert command_timeout == 1800
        captured.extend(arguments)
        (root / "run.sh").write_text("#!/bin/sh\n", encoding="utf-8")
        return {"exit_code": 0, "stdout": "installed", "stderr": ""}

    monkeypatch.setattr(operations.settings, "storage_root", tmp_path)
    monkeypatch.setattr(operations, "run_command", fake_run_command)
    result = await operations.install_minecraft(
        project_id,
        java_version=17,
        installer_jar=installer,
    )
    assert result["launch_mode"] == "forge_script"
    assert result["launcher"] == "run.sh"
    assert "--cap-drop" in captured
    assert captured[-4:] == ["java", "-jar", installer, "--installServer"]


@pytest.mark.asyncio
async def test_install_minecraft_downloads_official_version_pair(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Automatic Forge installation downloads and removes the verified installer."""
    project_id = "11111111-1111-1111-1111-111111111111"
    root = tmp_path / "minecraft" / project_id
    downloaded: list[tuple[str, str, Path]] = []
    ownership_changes: list[tuple[Path, int, int]] = []

    def fake_download(
        minecraft_version: str,
        forge_version: str,
        destination: Path,
    ) -> None:
        """Create a deterministic installer in place of an HTTPS download."""
        downloaded.append((minecraft_version, forge_version, destination))
        destination.write_bytes(b"verified-installer")

    async def fake_run_command(
        arguments: list[str],
        command_timeout: int = 60,
    ) -> dict[str, object]:
        """Capture the automatic installer and emulate Forge output."""
        assert command_timeout == 1800
        assert arguments[-2] == "forge-1.20.1-47.4.0-installer.jar"
        (root / "run.sh").write_text("#!/bin/sh\n", encoding="utf-8")
        return {"exit_code": 0, "stdout": "installed", "stderr": ""}

    def fake_chown(path: Path, uid: int, gid: int) -> None:
        """Capture installer ownership without requiring elevated privileges."""
        ownership_changes.append((Path(path), uid, gid))

    monkeypatch.setattr(operations.settings, "storage_root", tmp_path)
    monkeypatch.setattr(operations, "_download_forge_installer", fake_download)
    monkeypatch.setattr(operations, "run_command", fake_run_command)
    monkeypatch.setattr(operations.os, "chown", fake_chown)

    result = await operations.install_minecraft(
        project_id,
        java_version=17,
        minecraft_version="1.20.1",
        forge_version="47.4.0",
    )

    installer = root / "forge-1.20.1-47.4.0-installer.jar"
    assert downloaded == [("1.20.1", "47.4.0", installer)]
    assert ownership_changes == [(installer, root.stat().st_uid, root.stat().st_gid)]
    assert not installer.exists()
    assert result["minecraft_version"] == "1.20.1"
    assert result["forge_version"] == "47.4.0"


@pytest.mark.asyncio
async def test_minecraft_backup_flushes_and_resumes_world_writes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A running Minecraft backup brackets the archive with RCON save commands."""
    project_id = "11111111-1111-1111-1111-111111111111"
    commands: list[str] = []

    async def fake_running(target_project_id: str) -> bool:
        """Report the managed container as running."""
        assert target_project_id == project_id
        return True

    def fake_rcon(port: int, password: str, command: str) -> str:
        """Capture an authenticated RCON command."""
        assert port == 25575
        assert password == "correct-rcon-password"
        commands.append(command)
        return "ok"

    monkeypatch.setattr(operations, "_minecraft_container_running", fake_running)
    monkeypatch.setattr(operations, "_rcon_request", fake_rcon)
    prepared = await operations.minecraft_backup(
        project_id,
        action="prepare",
        rcon_host_port=25575,
        rcon_password="correct-rcon-password",
    )
    resumed = await operations.minecraft_backup(
        project_id,
        action="resume",
        rcon_host_port=25575,
        rcon_password="correct-rcon-password",
    )
    assert prepared["prepared"] is True
    assert resumed["prepared"] is False
    assert commands == ["save-off", "save-all flush", "save-on"]
