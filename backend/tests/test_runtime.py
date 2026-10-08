"""Project runtime WebSocket tests."""

import base64
import json
from typing import Any

import pytest
from fastapi.testclient import TestClient


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("§eHelp: §fIndex\n§7Use /help", "Help: Index\r\nUse /help"),
        ("first\r\nsecond\nthird\rlast", "first\r\nsecond\r\nthird\r\nlast"),
        ("§x§F§F§0§0§A§AHex §Lbold§R", "Hex bold"),
        ("§khidden§mstrike§nunder§oitalic§r", "hiddenstrikeunderitalic"),
        ("Привет §fмир\nУкраїна", "Привет мир\r\nУкраїна"),
        ("literal § and §z", "literal § and §z"),
        ("", ""),
    ],
)
def test_minecraft_terminal_output(value: str, expected: str) -> None:
    """RCON responses contain readable text and carriage-return line feeds."""
    from app.api.routes.runtime import _minecraft_terminal_output

    assert _minecraft_terminal_output(value) == expected


@pytest.mark.parametrize("engine", ["forge", "paper", "purpur", "spigot"])
def test_minecraft_terminal_formats_command_response(
    client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    engine: str,
) -> None:
    """Every Minecraft terminal normalizes RCON output before sending it."""
    from app.api.routes import runtime
    from app.schemas.runtime import ConsoleResult

    response = client.post(
        "/api/v1/projects",
        headers=auth_headers,
        json={
            "name": f"Console {engine}",
            "project_type": f"minecraft_{engine}",
            "runtime_type": f"minecraft_{engine}",
        },
    )
    assert response.status_code == 201
    project_id = response.json()["id"]

    async def fake_command(*args: Any, **kwargs: Any) -> ConsoleResult:
        """Return a representative multiline help response."""
        return ConsoleResult(exit_code=0, stdout="§eHelp\n§fCommands\r\n", stderr="")

    monkeypatch.setattr(runtime, "_execute_console_command", fake_command)
    token = auth_headers["Authorization"].removeprefix("Bearer ")
    with client.websocket_connect(
        f"/api/v1/projects/{project_id}/terminal/live?token={token}"
    ) as websocket:
        assert websocket.receive_json() == {"type": "ready"}
        websocket.receive_json()
        websocket.send_json(
            {"type": "input", "data": base64.b64encode(b"help\r").decode("ascii")}
        )
        output = ""
        for _ in range(4):
            message = websocket.receive_json()
            output += base64.b64decode(message["data"]).decode()
        assert output == "help\r\nHelp\r\nCommands\r\n> "


def _docker_project(client: TestClient, headers: dict[str, str]) -> str:
    """Create a Docker project for runtime tests."""
    response = client.post(
        "/api/v1/projects",
        headers=headers,
        json={
            "name": "Console Project",
            "project_type": "docker",
            "runtime_type": "docker",
        },
    )
    assert response.status_code == 201
    return str(response.json()["id"])


def _systemd_project(client: TestClient, headers: dict[str, str]) -> str:
    """Create an imported systemd project for runtime tests."""
    response = client.post(
        "/api/v1/projects",
        headers=headers,
        json={
            "name": "Imported Service",
            "project_type": "backend",
            "runtime_type": "systemd",
            "runtime_config": {"systemd_unit": "jannet-api.service"},
        },
    )
    assert response.status_code == 201
    return str(response.json()["id"])


def test_console_websocket_executes_and_audits_command(
    client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Authenticated console WebSocket returns correlated agent output."""
    from app.services.agent import AgentClient

    project_id = _docker_project(client, auth_headers)
    calls: list[tuple[str, dict[str, Any]]] = []

    async def fake_execute(
        self: AgentClient,
        operation: str,
        parameters: dict[str, Any] | None = None,
        *,
        request_timeout: float = 30,
    ) -> dict[str, Any]:
        """Capture one allowlisted agent command."""
        del self, request_timeout
        calls.append((operation, parameters or {}))
        return {"exit_code": 0, "stdout": "ready\n", "stderr": ""}

    monkeypatch.setattr(AgentClient, "execute", fake_execute)
    token = auth_headers["Authorization"].removeprefix("Bearer ")
    with client.websocket_connect(
        f"/api/v1/projects/{project_id}/console/live?token={token}"
    ) as websocket:
        assert websocket.receive_json() == {"type": "ready", "runtime_type": "docker"}
        websocket.send_json({"request_id": "request-1", "command": "printf ready"})
        assert websocket.receive_json() == {
            "type": "result",
            "request_id": "request-1",
            "exit_code": 0,
            "stdout": "ready\n",
            "stderr": "",
        }
    assert calls == [
        (
            "container_command",
            {
                "container": f"vps-project-{project_id}",
                "command": "printf ready",
            },
        )
    ]
    audit = client.get("/api/v1/audit-logs", headers=auth_headers)
    assert audit.status_code == 200
    event = next(item for item in audit.json() if item["action"] == "console.command")
    assert event["resource_id"] == project_id
    assert event["details"]["command_name"] == "printf"
    assert "ready" not in event["details"]


def test_console_websocket_rejects_invalid_payload_without_agent_call(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    """Malformed console payloads receive a bounded protocol error."""
    project_id = _docker_project(client, auth_headers)
    token = auth_headers["Authorization"].removeprefix("Bearer ")
    with client.websocket_connect(
        f"/api/v1/projects/{project_id}/console/live?token={token}"
    ) as websocket:
        websocket.receive_json()
        websocket.send_json({"request_id": "request-2", "command": ""})
        assert websocket.receive_json() == {
            "type": "error",
            "request_id": "request-2",
            "error": "Invalid console command",
        }


def test_imported_systemd_project_uses_configured_unit(
    client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Runtime operations for imported services use the stored systemd unit."""
    from app.services.agent import AgentClient

    project_id = _systemd_project(client, auth_headers)
    calls: list[tuple[str, dict[str, Any]]] = []

    async def fake_execute(
        self: AgentClient,
        operation: str,
        parameters: dict[str, Any] | None = None,
        *,
        request_timeout: float = 30,
    ) -> dict[str, Any]:
        """Capture the requested systemd unit."""
        del self, request_timeout
        calls.append((operation, parameters or {}))
        return {"exit_code": 0, "stdout": "active\n", "stderr": ""}

    monkeypatch.setattr(AgentClient, "execute", fake_execute)
    response = client.post(
        f"/api/v1/projects/{project_id}/console",
        headers=auth_headers,
        json={"command": "status"},
    )
    assert response.status_code == 200
    assert calls == [
        (
            "manage_service",
            {"service": "jannet-api.service", "action": "status"},
        )
    ]


@pytest.mark.parametrize(
    ("action", "expected_status"),
    [("start", "running"), ("stop", "stopped"), ("restart", "running")],
)
def test_runtime_lifecycle_action_updates_project_status(
    client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    action: str,
    expected_status: str,
) -> None:
    """Lifecycle endpoints call the configured runtime and persist its state."""
    from app.services.agent import AgentClient

    project_id = _systemd_project(client, auth_headers)
    calls: list[tuple[str, dict[str, Any]]] = []

    async def fake_execute(
        self: AgentClient,
        operation: str,
        parameters: dict[str, Any] | None = None,
        *,
        request_timeout: float = 30,
    ) -> dict[str, Any]:
        """Capture the lifecycle operation sent to the system agent."""
        del self, request_timeout
        calls.append((operation, parameters or {}))
        return {"exit_code": 0, "stdout": "", "stderr": ""}

    monkeypatch.setattr(AgentClient, "execute", fake_execute)
    response = client.post(
        f"/api/v1/projects/{project_id}/runtime/{action}",
        headers=auth_headers,
    )

    assert response.status_code == 200
    assert calls == [
        (
            "manage_service",
            {"service": "jannet-api.service", "action": action},
        )
    ]
    project = client.get(f"/api/v1/projects/{project_id}", headers=auth_headers)
    assert project.status_code == 200
    assert project.json()["status"] == expected_status


def test_terminal_message_validation_rejects_unbounded_or_invalid_frames() -> None:
    """Interactive terminal frames accept only bounded input and resize payloads."""
    from app.api.routes.runtime import _validated_terminal_message

    assert json.loads(_validated_terminal_message('{"type":"input","data":"cHdkDQo="}')) == {
        "type": "input",
        "data": "cHdkDQo=",
    }
    assert json.loads(
        _validated_terminal_message('{"type":"resize","cols":120,"rows":40}')
    ) == {"type": "resize", "cols": 120, "rows": 40}
    with pytest.raises(ValueError, match="terminal input"):
        _validated_terminal_message('{"type":"input","data":"not-base64"}')
    with pytest.raises(ValueError, match="terminal size"):
        _validated_terminal_message('{"type":"resize","cols":1,"rows":1}')


def test_interactive_terminal_proxies_agent_pty_and_audits_session(
    client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The browser terminal receives agent PTY frames and records session metadata."""
    from app.api.routes import runtime

    project_id = _systemd_project(client, auth_headers)
    project_update = client.patch(
        f"/api/v1/projects/{project_id}",
        headers=auth_headers,
        json={
            "runtime_config": {
                "systemd_unit": "jannet-api.service",
                "source_path": "/home/jannet-api",
            }
        },
    )
    assert project_update.status_code == 200

    class FakeAgentSocket:
        """Deterministic internal agent WebSocket."""

        def __init__(self) -> None:
            """Initialize captured messages and terminal output."""
            self.sent: list[str] = []
            self.messages = iter(
                [
                    '{"type":"ready"}',
                    '{"type":"output","data":"cmVhZHkNCg=="}',
                    '{"type":"exit","exit_code":0}',
                ]
            )

        async def send(self, message: str) -> None:
            """Capture one backend-to-agent message."""
            self.sent.append(message)

        def __aiter__(self) -> "FakeAgentSocket":
            """Return this socket as its async iterator."""
            return self

        async def __anext__(self) -> str:
            """Return the next deterministic agent frame."""
            try:
                return next(self.messages)
            except StopIteration as exc:
                raise StopAsyncIteration from exc

    class FakeAgentContext:
        """Async context manager for the fake agent socket."""

        def __init__(self, socket: FakeAgentSocket) -> None:
            """Store the socket returned on entry."""
            self.socket = socket

        async def __aenter__(self) -> FakeAgentSocket:
            """Return the fake socket."""
            return self.socket

        async def __aexit__(self, *args: object) -> None:
            """Close the fake context without suppressing errors."""

    agent_socket = FakeAgentSocket()

    def fake_connect(*args: object, **kwargs: object) -> FakeAgentContext:
        """Return the deterministic internal agent connection."""
        del args, kwargs
        return FakeAgentContext(agent_socket)

    monkeypatch.setattr(runtime, "connect_websocket", fake_connect)
    token = auth_headers["Authorization"].removeprefix("Bearer ")
    with client.websocket_connect(
        f"/api/v1/projects/{project_id}/terminal/live?token={token}"
    ) as websocket:
        assert websocket.receive_json() == {"type": "ready"}
        assert websocket.receive_json() == {
            "type": "output",
            "data": "cmVhZHkNCg==",
        }
        assert websocket.receive_json() == {"type": "exit", "exit_code": 0}

    descriptor = json.loads(agent_socket.sent[0])
    assert descriptor == {
        "runtime_type": "systemd",
        "project_id": project_id,
        "cols": 120,
        "rows": 32,
        "project_root": "/home/jannet-api",
        "service": "jannet-api.service",
    }
    audit = client.get("/api/v1/audit-logs", headers=auth_headers)
    assert audit.status_code == 200
    actions = {item["action"] for item in audit.json()}
    assert "console.terminal_opened" in actions
