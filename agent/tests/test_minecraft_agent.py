"""Minecraft artifact, plugin and protocol boundary tests."""

import asyncio
import hashlib
import io
import json
import os
import socket
import zipfile
from pathlib import Path
from typing import Any

import pytest
from system_agent import minecraft, operations

PROJECT_ID = "11111111-1111-1111-1111-111111111111"
JOB_ID = "22222222-2222-2222-2222-222222222222"


def write_jar(path: Path, *, plugin: bool = False) -> bytes:
    """Write a minimal runnable or plugin JAR descriptor for boundary tests."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            "plugin.yml" if plugin else "META-INF/MANIFEST.MF",
            "name: Test\nversion: 1.0\nmain: test.Main\n"
            if plugin
            else "Manifest-Version: 1.0\nMain-Class: test.Main\n",
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(buffer.getvalue())
    return buffer.getvalue()


def test_jar_validation_rejects_non_jars_missing_descriptors_and_symlinks(
    tmp_path: Path,
) -> None:
    """Invalid archives are rejected before any Java execution or file publication."""
    path = tmp_path / "server.jar"
    content = write_jar(path)
    assert minecraft.validate_jar(path) == hashlib.sha256(content).hexdigest()
    link = tmp_path / "link.jar"
    link.symlink_to(path)
    with pytest.raises(ValueError):
        minecraft.validate_jar(link)
    path.write_bytes(b"not-a-jar")
    with pytest.raises(ValueError):
        minecraft.validate_jar(path)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("README", "no descriptor")
    with pytest.raises(ValueError):
        minecraft.validate_jar(path)


@pytest.mark.asyncio
async def test_uploaded_jar_is_staged_and_published_only_after_verification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Staging preserves the live JAR and publication rejects altered artifacts."""
    monkeypatch.setattr(minecraft.settings, "storage_root", tmp_path)
    root = minecraft.project_root(PROJECT_ID)
    root.mkdir(parents=True)
    original = write_jar(root / "server.jar")
    write_jar(root / "uploaded.jar")
    result = await minecraft.stage_server(
        PROJECT_ID, JOB_ID, engine="spigot", uploaded_jar="uploaded.jar", java_version=21
    )
    assert (root / "server.jar").read_bytes() == original
    with pytest.raises(ValueError, match="changed"):
        minecraft.publish_server(PROJECT_ID, JOB_ID, "0" * 64)
    minecraft.publish_server(PROJECT_ID, JOB_ID, result["sha256"])
    assert minecraft.validate_jar(root / "server.jar") == result["sha256"]


@pytest.mark.asyncio
async def test_staging_rejects_symlink_upload_and_path_traversal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Even project-local aliases and uploaded traversal cannot escape staging."""
    monkeypatch.setattr(minecraft.settings, "storage_root", tmp_path)
    root = minecraft.project_root(PROJECT_ID)
    root.mkdir(parents=True)
    write_jar(root / "real.jar")
    (root / "alias.jar").symlink_to(root / "real.jar")
    for name in ["alias.jar", "../real.jar"]:
        with pytest.raises(ValueError):
            await minecraft.stage_server(PROJECT_ID, JOB_ID, engine="paper", uploaded_jar=name)


@pytest.mark.asyncio
async def test_plugin_changes_require_stopped_server_and_preserve_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Plugin enable, disable and removal never delete persistent plugin data."""
    monkeypatch.setattr(minecraft.settings, "storage_root", tmp_path)
    root = minecraft.project_root(PROJECT_ID)
    root.mkdir(parents=True)
    source = tmp_path / ".uploads" / "abcd"
    write_jar(source, plugin=True)
    running = True

    async def is_running(project_id: str) -> bool:
        """Expose deterministic container state for plugin mutation tests."""
        return running

    monkeypatch.setattr(operations, "_minecraft_container_running", is_running)
    with pytest.raises(ValueError, match="Stop"):
        await minecraft.plugin_operation(
            PROJECT_ID, action="upload", name="test.jar", staged_path=str(source)
        )
    running = False
    await minecraft.plugin_operation(
        PROJECT_ID, action="upload", name="test.jar", staged_path=str(source)
    )
    data = root / "plugins" / "test" / "data.txt"
    data.parent.mkdir()
    data.write_text("persistent")
    await minecraft.plugin_operation(PROJECT_ID, action="disable", name="test.jar")
    assert (await minecraft.plugin_operation(PROJECT_ID, action="list"))["plugins"][0][
        "enabled"
    ] is False
    await minecraft.plugin_operation(PROJECT_ID, action="enable", name="test.jar")
    await minecraft.plugin_operation(PROJECT_ID, action="delete", name="test.jar")
    assert data.read_text() == "persistent"


class FragmentedSocket:
    """A deterministic TCP stream that returns fragmented status packet data."""

    def __init__(self, content: bytes) -> None:
        """Store a test response and outgoing frame capture."""
        self.content = bytearray(content)
        self.sent = b""

    def __enter__(self) -> "FragmentedSocket":
        """Enter a fake socket context."""
        return self

    def __exit__(self, *args: Any) -> None:
        """Release the fake socket context."""

    def sendall(self, data: bytes) -> None:
        """Capture handshake and status request frames."""
        self.sent += data

    def settimeout(self, seconds: float) -> None:
        """Accept deadline updates while emulating an immediate local stream."""

    def recv(self, size: int) -> bytes:
        """Deliver at most one byte to exercise exact-read protocol logic."""
        data = bytes(self.content[:1])
        del self.content[:1]
        return data


def test_server_ping_handles_tcp_fragmentation(monkeypatch: pytest.MonkeyPatch) -> None:
    """Bounded protocol reads correctly decode JSON across fragmented TCP frames."""
    body = json.dumps(
        {
            "players": {"online": 3, "max": 20},
            "version": {"name": "Paper 1.21.1"},
            "description": "Test",
        }
    ).encode()
    packet = b"\x00" + minecraft._varint(len(body)) + body
    connection = FragmentedSocket(minecraft._varint(len(packet)) + packet)
    monkeypatch.setattr(socket, "create_connection", lambda *args, **kwargs: connection)
    assert minecraft._ping(25565)["online_players"] == 3
    assert b"127.0.0.1" in connection.sent
    assert connection.sent.endswith(b"\x01\x00")


def test_status_rejects_oversize_and_malformed_varints() -> None:
    """Malformed and excessive packet lengths fail without unbounded allocation."""
    for data in [b"\xff" * 5, minecraft._varint(1024 * 1024), b""]:
        with pytest.raises(ValueError):
            minecraft._read_varint(FragmentedSocket(data))  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_graceful_stop_does_not_force_remove_after_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stuck Java process prevents destructive update steps instead of SIGKILL."""
    monkeypatch.setattr(minecraft.settings, "storage_root", tmp_path)
    calls = []

    async def running(project_id: str) -> bool:
        """Simulate a permanently running container."""
        return True

    async def command(arguments: list[str], **kwargs: Any) -> dict[str, Any]:
        """Capture Docker argv without contacting the daemon."""
        calls.append(arguments)
        return {"stdout": "", "stderr": "", "exit_code": 0}

    monkeypatch.setattr(operations, "_minecraft_container_running", running)
    monkeypatch.setattr(operations, "run_command", command)
    monkeypatch.setattr(minecraft, "STOP_TIMEOUT_SECONDS", -1)
    with pytest.raises(ValueError, match="did not stop"):
        await minecraft.stop_server(PROJECT_ID)
    assert calls[0][1:3] == ["update", "--restart=no"]
    assert calls[1][1:3] == ["kill", "--signal=SIGTERM"]
    assert all("--force" not in arguments for arguments in calls)


def test_download_rejects_non_official_hosts_before_network(tmp_path: Path) -> None:
    """Artifact metadata cannot direct the privileged helper to arbitrary services."""
    for url in [
        "http://fill-data.papermc.io/a",
        "https://127.0.0.1/a",
        "https://fill-data.papermc.io.evil.example/a",
        "https://user:pass@fill-data.papermc.io/a",
    ]:
        with pytest.raises(ValueError, match="host"):
            minecraft.download(url, tmp_path / "server.jar", "paper")
    assert not (tmp_path / "server.part").exists()


def test_jar_validation_checks_crc_for_all_entries(tmp_path: Path) -> None:
    """Corruption outside the manifest is rejected before publication."""
    path = tmp_path / "server.jar"
    write_jar(path)
    with zipfile.ZipFile(path, "a") as archive:
        archive.writestr("code.bin", b"unique-class-payload")
    path.write_bytes(
        path.read_bytes().replace(b"unique-class-payload", b"broken-class-payload")
    )
    with pytest.raises(ValueError):
        minecraft.validate_jar(path)


@pytest.mark.parametrize(
    "major,image", [(16, "azul/zulu-openjdk:16-jre"), (25, "eclipse-temurin:25-jre")]
)
def test_legacy_and_modern_java_images(major: int, image: str) -> None:
    """Legacy runtime selection never references the removed Temurin 16 tag."""
    assert minecraft.java_image(major) == image
    assert minecraft.java_image(16, jdk=True) == "azul/zulu-openjdk:16"


@pytest.mark.asyncio
async def test_agent_fence_waits_for_in_flight_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Recovery cannot overtake a helper that survived its worker connection."""
    monkeypatch.setattr(minecraft.settings, "storage_root", tmp_path)
    entered = asyncio.Event()

    async def recover() -> None:
        """Wait for the old helper to complete before reconciling persistent state."""
        async with minecraft.mutation_lock(PROJECT_ID):
            entered.set()

    async with minecraft.mutation_lock(PROJECT_ID):
        task = asyncio.create_task(recover())
        await asyncio.sleep(0.01)
        assert not entered.is_set()
    await asyncio.wait_for(task, timeout=1)
    assert entered.is_set()


@pytest.mark.asyncio
async def test_dispatcher_fences_managed_file_operations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Generic archive helpers share the same project fence as Minecraft lifecycle."""
    from system_agent.dispatcher import dispatch_operation
    from system_agent.protocol import OperationRequest

    monkeypatch.setattr(minecraft.settings, "storage_root", tmp_path)
    result = await dispatch_operation(
        OperationRequest(operation="minecraft_fence", parameters={"project_id": PROJECT_ID})
    )
    assert result == {"idle": True}
    assert (tmp_path / ".minecraft-agent-locks" / f"{PROJECT_ID}.lock").is_file()


def test_properties_do_not_continue_into_managed_secrets(tmp_path: Path) -> None:
    """Legacy continuation and escaped managed keys cannot expose or override RCON."""
    path = tmp_path / "server.properties"
    path.write_text("motd=Welcome\\\nrcon\\u002epassword=attack\nserver\\u002dport=30000\n")
    operations._write_server_properties(
        tmp_path,
        game_port=25565,
        rcon_enabled=True,
        rcon_port=25575,
        rcon_password="a-private-rcon-secret",
    )
    content = path.read_text()
    assert "motd=Welcome\\\\\n" in content
    assert "attack" not in content
    assert "server-port=25565" in content
    assert "rcon.password=a-private-rcon-secret" in content


def test_properties_reject_fifo_without_blocking(tmp_path: Path) -> None:
    """Special files in server storage must never block a privileged file reader."""
    os.mkfifo(tmp_path / "server.properties")
    with pytest.raises(ValueError, match="regular file"):
        operations._write_server_properties(
            tmp_path, game_port=25565, rcon_enabled=False, rcon_port=25575, rcon_password=None
        )


def test_forge_jvm_arguments_reject_symlink(tmp_path: Path) -> None:
    """Existing Forge launchers retain the same host filesystem boundary."""
    outside = tmp_path / "outside.txt"
    outside.write_text("unchanged")
    (tmp_path / "user_jvm_args.txt").symlink_to(outside)
    with pytest.raises(ValueError):
        operations._write_forge_jvm_arguments(tmp_path, xms="1G", xmx="2G")
    assert outside.read_text() == "unchanged"


@pytest.mark.asyncio
async def test_start_rejects_changed_installed_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Launcher tampering is rejected before contacting Docker or writing secrets."""
    monkeypatch.setattr(minecraft.settings, "storage_root", tmp_path)
    root = minecraft.project_root(PROJECT_ID)
    root.mkdir(parents=True)
    write_jar(root / "server.jar")
    with pytest.raises(ValueError, match="installed artifact"):
        await operations.start_minecraft(
            PROJECT_ID,
            java_version=21,
            xms="256M",
            xmx="512M",
            server_jar="server.jar",
            game_port=25565,
            eula_accepted=True,
            server_sha256="0" * 64,
        )
    assert not (root / "server.properties").exists()
