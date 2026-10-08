"""System agent cleanup operation tests."""

import os
import pty
import pwd
import zipfile
from pathlib import Path
from typing import Any

import pytest
from system_agent import operations, terminal
from system_agent.protocol import TerminalOpenRequest

PROJECT_ID = "11111111-1111-1111-1111-111111111111"


class FakeProcess:
    """Minimal subprocess result used for existence checks."""

    def __init__(self, return_code: int) -> None:
        """Store the deterministic process exit code."""
        self._return_code = return_code

    async def wait(self) -> int:
        """Return the configured exit code."""
        return self._return_code


class EchoProcess:
    """Subprocess fake that echoes provided stdin."""

    returncode = 0

    async def communicate(self, stdin_data: bytes | None = None) -> tuple[bytes, bytes]:
        """Return stdin as stdout."""
        return stdin_data or b"", b""


async def test_cleanup_project_is_idempotent_for_static_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Static cleanup removes fixed project roots and tolerates a retry."""
    storage = tmp_path / "storage"
    systemd = tmp_path / "systemd"
    sshd = tmp_path / "sshd"
    for root in (storage, systemd, sshd):
        root.mkdir()
    for namespace in ("projects", "minecraft", "sftp"):
        project_root = storage / namespace / PROJECT_ID
        project_root.mkdir(parents=True)
        (project_root / "data.txt").write_text("managed", encoding="utf-8")
    monkeypatch.setattr(operations.settings, "storage_root", storage)
    monkeypatch.setattr(operations.settings, "systemd_unit_root", systemd)
    monkeypatch.setattr(operations.settings, "sshd_config_root", sshd)

    commands: list[list[str]] = []

    async def fake_run_command(
        arguments: list[str],
        command_timeout: int = 60,
        *,
        stdin_data: bytes | None = None,
    ) -> dict[str, Any]:
        """Return the escaped mount-unit name produced for a hyphenated path."""
        del command_timeout, stdin_data
        commands.append(arguments)
        assert "--suffix=mount" in arguments
        return {
            "exit_code": 0,
            "stdout": (
                r"srv-vps\x2dpanel-sftp-"
                r"11111111\x2d1111\x2d1111\x2d1111\x2d111111111111"
                "-uploads.mount\n"
            ),
            "stderr": "",
        }

    async def fake_subprocess(*arguments: object, **kwargs: object) -> FakeProcess:
        """Report that the deterministic SFTP account does not exist."""
        del arguments, kwargs
        return FakeProcess(1)

    monkeypatch.setattr(operations, "run_command", fake_run_command)
    monkeypatch.setattr(operations.asyncio, "create_subprocess_exec", fake_subprocess)

    first = await operations.cleanup_project(PROJECT_ID, "static", [])
    second = await operations.cleanup_project(PROJECT_ID, "static", [])

    assert sorted(first["removed_directories"]) == [
        "minecraft/" + PROJECT_ID,
        "projects/" + PROJECT_ID,
        "sftp/" + PROJECT_ID,
    ]
    assert second["removed_directories"] == []
    assert len(commands) == 4


async def test_sftp_mount_unit_name_rejects_invalid_escape(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mount-unit validation accepts only systemd hexadecimal escape sequences."""

    async def fake_run_command(
        arguments: list[str],
        command_timeout: int = 60,
        *,
        stdin_data: bytes | None = None,
    ) -> dict[str, Any]:
        """Return a malformed unit name from the mocked systemd helper."""
        del arguments, command_timeout, stdin_data
        return {
            "exit_code": 0,
            "stdout": r"srv-vps\q2dpanel.mount",
            "stderr": "",
        }

    monkeypatch.setattr(operations, "run_command", fake_run_command)

    with pytest.raises(ValueError, match="Invalid generated mount unit name"):
        await operations._sftp_mount_unit_name(tmp_path / "uploads")


async def test_run_command_opens_stdin_pipe_when_input_is_provided(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Privileged helper requests are passed through subprocess stdin."""
    captured: dict[str, object] = {}

    async def fake_subprocess(*arguments: object, **kwargs: object) -> EchoProcess:
        """Capture subprocess stdin configuration."""
        del arguments
        captured["stdin"] = kwargs.get("stdin")
        return EchoProcess()

    monkeypatch.setattr(operations.asyncio, "create_subprocess_exec", fake_subprocess)
    result = await operations.run_command(["/bin/helper"], stdin_data=b"payload")

    assert captured["stdin"] == operations.asyncio.subprocess.PIPE
    assert result["stdout"] == "payload"


def test_list_directory_rejects_path_escape(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Imported project listing cannot escape its root."""
    root = tmp_path / "home" / "project"
    root.mkdir(parents=True)
    monkeypatch.setattr(operations, "EXTERNAL_PROJECT_ROOTS", (tmp_path / "home",))
    with pytest.raises(ValueError, match="escapes"):
        operations.list_directory(str(root), "../other")


def test_list_directory_lists_imported_project_root(monkeypatch: pytest.MonkeyPatch) -> None:
    """Imported project listing returns bounded file metadata."""
    root = Path("/tmp/nectarine-agent-imported")
    root.mkdir(exist_ok=True)
    (root / "app.py").write_text("print('ok')", encoding="utf-8")
    monkeypatch.setattr(operations, "EXTERNAL_PROJECT_ROOTS", (Path("/tmp"),))

    result = operations.list_directory(str(root))

    assert result["entries"][0]["name"] == "app.py"


def test_list_directory_reports_recursive_folder_size(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Privileged imported-project listings include recursive directory sizes."""
    root = tmp_path / "home" / "project"
    nested = root / "storage" / "nested"
    nested.mkdir(parents=True)
    (nested / "data.bin").write_bytes(b"x" * 256)
    monkeypatch.setattr(operations, "EXTERNAL_PROJECT_ROOTS", (tmp_path / "home",))

    result = operations.list_directory(str(root))

    storage = next(item for item in result["entries"] if item["name"] == "storage")
    assert storage["kind"] == "directory"
    assert storage["size_bytes"] == 256


def test_list_directory_classifies_internal_directory_symlink(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Imported roots expose safe internal directory links as browsable."""
    root = tmp_path / "home" / "project"
    release = root / "releases" / "release-1"
    release.mkdir(parents=True)
    (release / "app.txt").write_text("deployed", encoding="utf-8")
    (root / "current").symlink_to(release, target_is_directory=True)
    monkeypatch.setattr(operations, "EXTERNAL_PROJECT_ROOTS", (tmp_path / "home",))

    result = operations.list_directory(str(root))
    current = next(entry for entry in result["entries"] if entry["name"] == "current")

    assert current["kind"] == "symlink"
    assert current["target_kind"] == "directory"
    assert current["size_bytes"] == len("deployed")


def test_delete_path_removes_entry_without_following_symlink(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Privileged deletion removes only the confirmed imported-project entry."""
    root = tmp_path / "home" / "project"
    root.mkdir(parents=True)
    outside = tmp_path / "outside.txt"
    outside.write_text("keep", encoding="utf-8")
    link = root / "link"
    link.symlink_to(outside)
    target = root / "remove.txt"
    target.write_text("remove", encoding="utf-8")
    monkeypatch.setattr(operations, "EXTERNAL_PROJECT_ROOTS", (tmp_path / "home",))

    assert operations.delete_path(str(root), "remove.txt") == {"removed": "remove.txt"}
    assert not target.exists()
    assert operations.delete_path(str(root), "link") == {"removed": "link"}
    assert not link.is_symlink()
    assert outside.read_text(encoding="utf-8") == "keep"


def test_delete_path_rejects_parent_traversal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Privileged deletion rejects paths outside the imported project root."""
    root = tmp_path / "home" / "project"
    root.mkdir(parents=True)
    monkeypatch.setattr(operations, "EXTERNAL_PROJECT_ROOTS", (tmp_path / "home",))

    with pytest.raises(ValueError, match="Invalid project path"):
        operations.delete_path(str(root), "../outside.txt")


def test_move_path_moves_entry_inside_imported_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Privileged moves remain inside the imported project root."""
    root = tmp_path / "home" / "project"
    source = root / "source.txt"
    source.parent.mkdir(parents=True)
    source.write_text("payload", encoding="utf-8")
    monkeypatch.setattr(operations, "EXTERNAL_PROJECT_ROOTS", (tmp_path / "home",))

    result = operations.move_path(str(root), "source.txt", "archive/source.txt")

    assert result["entry"]["path"] == "archive/source.txt"
    assert result["entry"]["size_bytes"] == 7
    assert not source.exists()
    assert (root / "archive/source.txt").is_file()


def test_stage_file_download_copies_protected_imported_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Protected files are staged below private panel storage for streaming."""
    root = tmp_path / "home" / "project"
    root.mkdir(parents=True)
    source = root / "server.js"
    source.write_bytes(b"console.log('ok')")
    storage = tmp_path / "storage"
    monkeypatch.setattr(operations, "EXTERNAL_PROJECT_ROOTS", (tmp_path / "home",))
    monkeypatch.setattr(operations.settings, "storage_root", storage)
    monkeypatch.setattr(
        operations.settings,
        "project_runtime_user",
        pwd.getpwuid(os.getuid()).pw_name,
    )

    result = operations.stage_file_download(
        str(root),
        "server.js",
        "a" * 32,
    )

    staged = Path(result["path"])
    assert staged == storage / ".downloads" / ("a" * 32)
    assert staged.read_bytes() == source.read_bytes()
    assert result["size_bytes"] == len(b"console.log('ok')")


def test_publish_staged_upload_writes_protected_imported_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Panel-owned staged uploads can be safely published by the agent."""
    root = tmp_path / "home" / "project"
    root.mkdir(parents=True)
    storage = tmp_path / "storage"
    staged = storage / ".uploads" / "upload"
    staged.parent.mkdir(parents=True)
    staged.write_bytes(b"payload")
    monkeypatch.setattr(operations, "EXTERNAL_PROJECT_ROOTS", (tmp_path / "home",))
    monkeypatch.setattr(operations.settings, "storage_root", storage)

    result = operations.publish_staged_upload(str(root), "data/app.txt", str(staged))

    assert result["size_bytes"] == 7
    assert (root / "data/app.txt").read_bytes() == b"payload"
    assert staged.read_bytes() == b"payload"


def test_publish_staged_upload_preserves_runtime_ownership(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """New uploads and nested directories inherit the project's runtime owner."""
    root = tmp_path / "home" / "project"
    root.mkdir(parents=True)
    storage = tmp_path / "storage"
    staged = storage / ".uploads" / "upload"
    staged.parent.mkdir(parents=True)
    staged.write_bytes(b"database payload")
    monkeypatch.setattr(operations, "EXTERNAL_PROJECT_ROOTS", (tmp_path / "home",))
    monkeypatch.setattr(operations.settings, "storage_root", storage)
    ownership: list[tuple[Path, int, int]] = []

    def capture_chown(path: Path, uid: int, gid: int) -> None:
        """Capture the ownership applied before atomic publication."""
        ownership.append((path, uid, gid))

    monkeypatch.setattr(operations.os, "chown", capture_chown)
    operations.publish_staged_upload(str(root), "data/nested/applications.sqlite3", str(staged))

    owner = root.stat()
    assert [path for path, _, _ in ownership[:2]] == [root / "data", root / "data/nested"]
    assert ownership[2][0].name.endswith(".upload")
    assert len(ownership) == 3
    assert all(uid == owner.st_uid and gid == owner.st_gid for _, uid, gid in ownership)
    assert (root / "data/nested/applications.sqlite3").read_bytes() == b"database payload"
    assert (root / "data/nested").stat().st_mode & 0o777 == 0o750


def test_archive_path_archives_imported_directory_without_links(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Imported directories are archived by the agent without following links."""
    root = tmp_path / "home" / "project"
    source = root / "src"
    source.mkdir(parents=True)
    (source / "app.js").write_text("safe", encoding="utf-8")
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    (source / "outside").symlink_to(outside)
    monkeypatch.setattr(operations, "EXTERNAL_PROJECT_ROOTS", (tmp_path / "home",))

    result = operations.archive_path(str(root), "src", "src.zip")

    archive = root / "src.zip"
    with zipfile.ZipFile(archive) as handle:
        names = handle.namelist()
        assert handle.read("src/app.js") == b"safe"
    assert "src/outside" not in names
    assert result["size_bytes"] == archive.stat().st_size


def test_extract_archive_allows_the_archive_parent_as_destination(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An imported archive can safely extract beside itself without overwrites."""
    root = tmp_path / "home" / "project"
    root.mkdir(parents=True)
    archive = root / "source.zip"
    with zipfile.ZipFile(archive, "x") as handle:
        handle.writestr("content/app.txt", "safe")
    monkeypatch.setattr(operations, "EXTERNAL_PROJECT_ROOTS", (tmp_path / "home",))

    result = operations.extract_archive(str(root), "source.zip", ".")

    assert result["path"] == "."
    assert (root / "content/app.txt").read_text(encoding="utf-8") == "safe"


async def test_cleanup_rejects_unmanaged_systemd_unit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cleanup never removes a fixed-name unit without its ownership marker."""
    systemd = tmp_path / "systemd"
    systemd.mkdir()
    unit = systemd / f"vps-project-{PROJECT_ID}.service"
    unit.write_text("[Service]\nExecStart=/bin/true\n", encoding="utf-8")
    monkeypatch.setattr(operations.settings, "systemd_unit_root", systemd)

    with pytest.raises(ValueError, match="not managed"):
        await operations.cleanup_project(PROJECT_ID, "systemd", [])
    assert unit.is_file()


async def test_project_runtime_user_is_deterministic_and_removed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Runtime user cleanup removes only the deterministic project account."""
    username = operations._project_runtime_username(PROJECT_ID)
    commands: list[list[str]] = []

    def fake_getpwnam(value: str) -> object:
        """Return an account only for the expected project runtime user."""
        if value != username:
            raise KeyError(value)
        return object()

    async def fake_run_command(
        arguments: list[str],
        command_timeout: int = 60,
        *,
        stdin_data: bytes | None = None,
    ) -> dict[str, Any]:
        """Capture user deletion without touching host accounts."""
        del command_timeout, stdin_data
        commands.append(arguments)
        return {"exit_code": 0, "stdout": "", "stderr": ""}

    monkeypatch.setattr(operations.pwd, "getpwnam", fake_getpwnam)
    monkeypatch.setattr(operations, "run_command", fake_run_command)
    removed = await operations._remove_project_runtime_user(PROJECT_ID)

    assert removed is True
    assert commands == [[str(operations.settings.userdel_binary), username]]


async def test_manage_service_accepts_imported_unit_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Imported services can be managed without fixed project unit names."""
    commands: list[list[str]] = []

    async def fake_run_command(
        arguments: list[str],
        command_timeout: int = 60,
        *,
        stdin_data: bytes | None = None,
    ) -> dict[str, Any]:
        """Capture the systemctl call."""
        del command_timeout, stdin_data
        commands.append(arguments)
        return {"exit_code": 0, "stdout": "active\n", "stderr": ""}

    monkeypatch.setattr(operations, "run_command", fake_run_command)
    await operations.manage_service("jannet-api.service", "status")
    assert commands == [
        [str(operations.settings.systemctl_binary), "is-active", "jannet-api.service"]
    ]


async def test_run_project_command_uses_runtime_user_cwd_and_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Project cron commands run as the deterministic user inside current release."""
    storage = tmp_path / "storage"
    current = storage / "projects" / PROJECT_ID / "current"
    shared = storage / "projects" / PROJECT_ID / "shared"
    current.mkdir(parents=True)
    shared.mkdir()
    (shared / "runtime.env").write_text("APP_ENV=production\n", encoding="utf-8")
    monkeypatch.setattr(operations.settings, "storage_root", storage)
    monkeypatch.setattr(operations.settings, "runuser_binary", Path("/usr/sbin/runuser"))
    captured: dict[str, Any] = {}

    async def fake_run_command(
        arguments: list[str],
        command_timeout: int = 60,
        *,
        stdin_data: bytes | None = None,
        output_limit: int = 32768,
        cwd: Path | None = None,
        env_overrides: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Capture the restricted runtime execution envelope."""
        del command_timeout, stdin_data, output_limit
        captured["arguments"] = arguments
        captured["cwd"] = cwd
        captured["env_overrides"] = env_overrides
        return {"exit_code": 0, "stdout": "ok", "stderr": ""}

    monkeypatch.setattr(operations, "run_command", fake_run_command)
    result = await operations.run_project_command(PROJECT_ID, "python manage.py task")

    assert result["stdout"] == "ok"
    assert captured["arguments"] == [
        "/usr/sbin/runuser",
        "-u",
        operations._project_runtime_username(PROJECT_ID),
        "--",
        "/bin/sh",
        "-lc",
        "python manage.py task",
    ]
    assert captured["cwd"] == current
    assert captured["env_overrides"] == {"APP_ENV": "production"}


async def test_compose_command_uses_fixed_compose_project(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Compose cron commands execute inside one validated service."""
    storage = tmp_path / "storage"
    current = storage / "projects" / PROJECT_ID / "current"
    current.mkdir(parents=True)
    compose_file = current / "docker-compose.yml"
    compose_file.write_text("services:\n  app:\n    image: nginx\n", encoding="utf-8")
    monkeypatch.setattr(operations.settings, "storage_root", storage)
    monkeypatch.setattr(operations.settings, "docker_binary", Path("/usr/bin/docker"))
    commands: list[list[str]] = []

    async def fake_run_command(
        arguments: list[str],
        command_timeout: int = 60,
        *,
        stdin_data: bytes | None = None,
        output_limit: int = 32768,
    ) -> dict[str, Any]:
        """Capture docker compose exec argv."""
        del command_timeout, stdin_data, output_limit
        commands.append(arguments)
        return {"exit_code": 0, "stdout": "ok", "stderr": ""}

    monkeypatch.setattr(operations, "run_command", fake_run_command)
    await operations.compose_command(PROJECT_ID, "app", "python manage.py task")

    assert commands == [
        [
            "/usr/bin/docker",
            "compose",
            "--project-name",
            f"vps-project-{PROJECT_ID}",
            "--project-directory",
            str(current),
            "--file",
            str(compose_file),
            "exec",
            "-T",
            "app",
            "/bin/sh",
            "-lc",
            "python manage.py task",
        ]
    ]


async def test_container_cleanup_checks_ownership_label(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A colliding container name is preserved when its project label differs."""

    async def fake_run_command(
        arguments: list[str],
        command_timeout: int = 60,
        *,
        stdin_data: bytes | None = None,
    ) -> dict[str, Any]:
        """Return an unexpected label from Docker inspect."""
        del arguments, command_timeout, stdin_data
        return {"exit_code": 0, "stdout": "another-project\n", "stderr": ""}

    monkeypatch.setattr(operations, "run_command", fake_run_command)
    with pytest.raises(ValueError, match="ownership label"):
        await operations._remove_project_container(PROJECT_ID)


async def test_project_metrics_reads_docker_stats(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Project metrics are collected from the fixed Docker container."""
    commands: list[list[str]] = []

    async def fake_run_command(
        arguments: list[str],
        command_timeout: int = 60,
        *,
        stdin_data: bytes | None = None,
        output_limit: int = 32768,
    ) -> dict[str, Any]:
        """Return one deterministic Docker stats row."""
        del command_timeout, stdin_data, output_limit
        commands.append(arguments)
        assert arguments[:4] == [
            str(operations.settings.docker_binary),
            "stats",
            "--no-stream",
            "--format",
        ]
        return {
            "exit_code": 0,
            "stdout": ('{"CPUPerc":"12.50%","MemUsage":"128MiB / 1GiB","NetIO":"2kB / 4kB"}\n'),
            "stderr": "",
        }

    monkeypatch.setattr(operations, "run_command", fake_run_command)
    result = await operations.project_metrics(PROJECT_ID, "docker")

    assert commands[0][-1] == f"vps-project-{PROJECT_ID}"
    assert result["resource_status"] == "running"
    assert result["cpu_percent"] == 12.5
    assert result["memory_used"] == 128 * 1024 * 1024
    assert result["memory_total"] == 1024 * 1024 * 1024
    assert result["network_bytes_received"] == 2000
    assert result["network_bytes_sent"] == 4000


async def test_project_metrics_use_imported_service_and_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Imported systemd metrics use their configured unit and protected root."""
    root = tmp_path / "home" / "project"
    root.mkdir(parents=True)
    (root / "data.bin").write_bytes(b"x" * 128)
    commands: list[list[str]] = []

    async def fake_run_command(
        arguments: list[str],
        command_timeout: int = 60,
        *,
        stdin_data: bytes | None = None,
    ) -> dict[str, Any]:
        """Return one deterministic service PID."""
        del command_timeout, stdin_data
        commands.append(arguments)
        return {"exit_code": 0, "stdout": "321\n", "stderr": ""}

    async def fake_process_metrics(pid: int) -> dict[str, Any]:
        """Return deterministic process metrics for the configured service."""
        assert pid == 321
        return {
            "resource_status": "running",
            "cpu_percent": 3.5,
            "memory_percent": 1.25,
            "memory_used": 64,
            "memory_total": 1024,
            "network_bytes_sent": 0,
            "network_bytes_received": 0,
            "process_count": 2,
        }

    monkeypatch.setattr(operations, "EXTERNAL_PROJECT_ROOTS", (tmp_path / "home",))
    monkeypatch.setattr(operations, "run_command", fake_run_command)
    monkeypatch.setattr(operations, "_process_tree_metrics", fake_process_metrics)

    result = await operations.project_metrics(
        PROJECT_ID,
        "systemd",
        service="jannet-api.service",
        project_root=str(root),
    )

    assert commands == [
        [
            str(operations.settings.systemctl_binary),
            "show",
            "jannet-api.service",
            "--property=MainPID",
            "--value",
        ]
    ]
    assert result["cpu_percent"] == 3.5
    assert result["disk_bytes"] == 128


async def test_remove_certificate_uses_exact_certbot_name(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Certificate deletion requires existing renewal state and fixed argv."""
    renewal = tmp_path / "renewal"
    renewal.mkdir()
    (renewal / "app.example.com.conf").write_text("managed", encoding="utf-8")
    monkeypatch.setattr(operations.settings, "letsencrypt_root", tmp_path)
    monkeypatch.setattr(operations.settings, "certbot_binary", Path("/usr/bin/certbot"))

    commands: list[list[str]] = []

    async def fake_run_command(
        arguments: list[str],
        command_timeout: int = 60,
        *,
        stdin_data: bytes | None = None,
    ) -> dict[str, Any]:
        """Capture the exact non-shell Certbot command."""
        del command_timeout, stdin_data
        commands.append(arguments)
        return {"exit_code": 0, "stdout": "deleted", "stderr": ""}

    monkeypatch.setattr(operations, "run_command", fake_run_command)
    missing = await operations.remove_certificate("missing.example.com")
    deleted = await operations.remove_certificate("app.example.com")

    assert missing["deleted"] is False
    assert deleted["deleted"] is True
    assert commands == [
        [
            "/usr/bin/certbot",
            "delete",
            "--cert-name",
            "app.example.com",
            "--non-interactive",
        ]
    ]


async def test_certificate_info_parses_expiry_and_fingerprint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """OpenSSL metadata is normalized for persistence without certificate contents."""
    certificate = tmp_path / "live" / "app.example.com" / "cert.pem"
    certificate.parent.mkdir(parents=True)
    certificate.write_text("certificate", encoding="utf-8")
    monkeypatch.setattr(operations.settings, "letsencrypt_root", tmp_path)
    monkeypatch.setattr(operations.settings, "openssl_binary", Path("/usr/bin/openssl"))
    fingerprint = ":".join(["AA"] * 32)

    async def fake_run_command(
        arguments: list[str],
        command_timeout: int = 60,
        *,
        stdin_data: bytes | None = None,
        output_limit: int = 32768,
    ) -> dict[str, Any]:
        """Return deterministic OpenSSL certificate metadata."""
        del command_timeout, stdin_data, output_limit
        assert arguments[:2] == ["/usr/bin/openssl", "x509"]
        return {
            "exit_code": 0,
            "stdout": (
                f"notAfter=Jun 29 12:00:00 2027 GMT\nsha256 Fingerprint={fingerprint}\n"
            ),
            "stderr": "",
        }

    monkeypatch.setattr(operations, "run_command", fake_run_command)
    result = await operations.certificate_info("app.example.com")
    assert result == {
        "hostname": "app.example.com",
        "expires_at": "2027-06-29T12:00:00+00:00",
        "fingerprint_sha256": "aa" * 32,
    }


def test_docker_terminal_is_scoped_to_the_project_container(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Docker terminals execute a fixed shell only inside the named container."""
    monkeypatch.setattr(terminal.settings, "docker_binary", Path("/usr/bin/docker"))
    request = TerminalOpenRequest(runtime_type="docker", project_id=PROJECT_ID)

    arguments, cwd, environment = terminal._terminal_process(request)

    assert arguments == [
        "/usr/bin/docker",
        "exec",
        "--interactive",
        "--tty",
        "--env",
        "TERM=xterm-256color",
        f"vps-project-{PROJECT_ID}",
        "/bin/sh",
    ]
    assert cwd is None
    assert environment["TERM"] == "xterm-256color"


def test_systemd_terminal_drops_to_a_non_root_project_account(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Host-backed terminals use the resolved project account and directory."""
    import pwd

    account = pwd.struct_passwd(
        ("project-user", "x", 1001, 1001, "", "/home/project-user", "/bin/bash")
    )
    monkeypatch.setattr(terminal, "_project_root", lambda value: tmp_path)
    monkeypatch.setattr(
        terminal,
        "_service_user",
        lambda service, root, project_id: account,
    )
    monkeypatch.setattr(terminal.settings, "runuser_binary", Path("/usr/sbin/runuser"))
    request = TerminalOpenRequest(
        runtime_type="systemd",
        project_id=PROJECT_ID,
        project_root="/srv/project",
        service="example.service",
    )

    arguments, cwd, environment = terminal._terminal_process(request)

    assert arguments == [
        "/usr/sbin/runuser",
        "-u",
        "project-user",
        "--",
        "/bin/bash",
        "--noprofile",
        "--norc",
    ]
    assert cwd == tmp_path
    assert environment["HOME"] == "/home/project-user"


def test_terminal_process_has_controlling_tty_and_job_control() -> None:
    """Interactive shells receive a controlling PTY with job control enabled."""
    master, slave = pty.openpty()
    process = None
    try:
        process = terminal._spawn_terminal_process(
            [
                "/bin/bash",
                "--noprofile",
                "--norc",
                "-i",
                "-c",
                "[[ -o monitor ]] && printf '__JOB_CONTROL__\\n'",
            ],
            None,
            {
                "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
                "LANG": "C.UTF-8",
                "TERM": "xterm-256color",
            },
            slave,
        )
        os.close(slave)
        slave = -1
        output = bytearray()
        while True:
            try:
                chunk = os.read(master, 65536)
            except OSError:
                break
            if not chunk:
                break
            output.extend(chunk)
        assert process.wait(timeout=5) == 0
        decoded = output.decode(errors="replace")
        assert "__JOB_CONTROL__" in decoded
        assert "cannot set terminal process group" not in decoded
        assert "no job control in this shell" not in decoded
    finally:
        if slave >= 0:
            os.close(slave)
        os.close(master)
        if process is not None and process.poll() is None:
            process.kill()
            process.wait(timeout=5)
