"""Interactive PTY execution for strictly scoped project runtimes."""

import base64
import binascii
import contextlib
import fcntl
import functools
import json
import os
import pty
import pwd
import selectors
import signal
import struct
import subprocess
import sys
import termios
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from system_agent import operations
from system_agent.config import settings
from system_agent.protocol import (
    TerminalInputMessage,
    TerminalOpenRequest,
    TerminalResizeMessage,
    TerminalRuntime,
)

MAX_CONTROL_LINE_BYTES = 96 * 1024
MAX_INPUT_BYTES = 64 * 1024


def _write_message(payload: dict[str, Any]) -> None:
    """Write one compact control message to the unprivileged agent."""
    sys.stdout.write(json.dumps(payload, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def _resize_terminal(file_descriptor: int, cols: int, rows: int) -> None:
    """Apply a validated terminal window size to a PTY."""
    dimensions = struct.pack("HHHH", rows, cols, 0, 0)
    fcntl.ioctl(file_descriptor, termios.TIOCSWINSZ, dimensions)


def _project_root(value: str) -> Path:
    """Resolve an imported or managed root under an allowlisted location."""
    root = operations._external_project_root(value)
    if not root.is_dir():
        raise ValueError("Project terminal root does not exist")
    return root


def _service_user(
    service: str,
    root: Path,
    project_id: str,
) -> pwd.struct_passwd:
    """Resolve a non-root account that owns the service or project directory."""
    result = subprocess.run(  # noqa: S603 - fixed binary and validated service argv
        [
            str(settings.systemctl_binary),
            "show",
            "--property=User",
            "--value",
            service,
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    candidates = [result.stdout.strip()]
    with contextlib.suppress(KeyError):
        candidates.append(pwd.getpwuid(root.stat().st_uid).pw_name)
    candidates.append(operations._project_runtime_username(project_id))
    for username in candidates:
        if not username or username == "root":
            continue
        try:
            account = pwd.getpwnam(username)
        except KeyError:
            continue
        if account.pw_uid > 0:
            return account
    raise ValueError("Root-owned services cannot expose a host terminal")


def _compose_service(request: TerminalOpenRequest, compose_file: Path) -> str:
    """Select one validated running Compose service for the terminal."""
    if request.compose_service is not None:
        return request.compose_service
    result = subprocess.run(  # noqa: S603 - fixed Docker argv and validated project ID
        [
            str(settings.docker_binary),
            "compose",
            "--project-name",
            f"vps-project-{request.project_id}",
            "--project-directory",
            str(compose_file.parent),
            "--file",
            str(compose_file),
            "ps",
            "--services",
            "--status",
            "running",
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )
    services = [
        line.strip()
        for line in result.stdout.splitlines()
        if operations.COMPOSE_SERVICE_PATTERN.fullmatch(line.strip())
    ]
    if not services:
        raise ValueError("Docker Compose has no running service")
    return services[0]


def _terminal_process(
    request: TerminalOpenRequest,
) -> tuple[list[str], Path | None, dict[str, str]]:
    """Build a fixed command without exposing a host root shell."""
    environment = {
        "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        "LANG": "C.UTF-8",
        "TERM": "xterm-256color",
        "COLORTERM": "truecolor",
    }
    if request.runtime_type == TerminalRuntime.DOCKER:
        return (
            [
                str(settings.docker_binary),
                "exec",
                "--interactive",
                "--tty",
                "--env",
                "TERM=xterm-256color",
                f"vps-project-{request.project_id}",
                "/bin/sh",
            ],
            None,
            environment,
        )
    if request.runtime_type == TerminalRuntime.DOCKER_COMPOSE:
        compose_file = operations._compose_file_for_project(request.project_id)
        service = _compose_service(request, compose_file)
        return (
            [
                str(settings.docker_binary),
                "compose",
                "--project-name",
                f"vps-project-{request.project_id}",
                "--project-directory",
                str(compose_file.parent),
                "--file",
                str(compose_file),
                "exec",
                "--env",
                "TERM=xterm-256color",
                service,
                "/bin/sh",
            ],
            compose_file.parent,
            environment,
        )
    if request.project_root is None or request.service is None:
        raise ValueError("Project terminal metadata is incomplete")
    root = _project_root(request.project_root)
    account = _service_user(request.service, root, request.project_id)
    environment.update(
        {
            "HOME": account.pw_dir,
            "LOGNAME": account.pw_name,
            "SHELL": "/bin/bash",
            "USER": account.pw_name,
        }
    )
    return (
        [
            str(settings.runuser_binary),
            "-u",
            account.pw_name,
            "--",
            "/bin/bash",
            "--noprofile",
            "--norc",
        ],
        root,
        environment,
    )


def _decode_input(message: TerminalInputMessage) -> bytes:
    """Decode bounded base64 terminal input."""
    try:
        value = base64.b64decode(message.data, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("Invalid terminal input encoding") from exc
    if len(value) > MAX_INPUT_BYTES:
        raise ValueError("Terminal input exceeds the maximum size")
    return value


def _write_all(file_descriptor: int, value: bytes) -> None:
    """Write a complete bounded input frame to the PTY."""
    remaining = memoryview(value)
    while remaining:
        written = os.write(file_descriptor, remaining)
        remaining = remaining[written:]


def _configure_controlling_terminal(slave: int) -> None:
    """Create a session and assign its controlling terminal before exec."""
    os.setsid()
    fcntl.ioctl(slave, termios.TIOCSCTTY, 0)


def _spawn_terminal_process(
    arguments: list[str],
    cwd: Path | None,
    environment: dict[str, str],
    slave: int,
) -> subprocess.Popen[bytes]:
    """Start a process group with the PTY slave as its controlling terminal."""
    return subprocess.Popen(  # noqa: S603 - argv is built from validated fields
        arguments,
        stdin=slave,
        stdout=slave,
        stderr=slave,
        cwd=str(cwd) if cwd else None,
        env=environment,
        close_fds=True,
        preexec_fn=functools.partial(_configure_controlling_terminal, slave),
    )


def _handle_control_line(master: int, raw_line: bytes) -> None:
    """Validate and apply one input or resize control message."""
    if len(raw_line) > MAX_CONTROL_LINE_BYTES:
        raise ValueError("Terminal control message is too large")
    payload = json.loads(raw_line)
    if not isinstance(payload, dict):
        raise ValueError("Invalid terminal control message")
    if payload.get("type") == "input":
        input_message = TerminalInputMessage.model_validate(payload)
        value = _decode_input(input_message)
        if value:
            _write_all(master, value)
        return
    if payload.get("type") == "resize":
        resize_message = TerminalResizeMessage.model_validate(payload)
        _resize_terminal(master, resize_message.cols, resize_message.rows)
        return
    raise ValueError("Unsupported terminal control message")


def run_terminal(request: TerminalOpenRequest) -> int:
    """Run one PTY until the client disconnects or its shell exits."""
    arguments, cwd, environment = _terminal_process(request)
    master, slave = pty.openpty()
    _resize_terminal(slave, request.cols, request.rows)
    process: subprocess.Popen[bytes] | None = None
    selector = selectors.DefaultSelector()
    try:
        process = _spawn_terminal_process(arguments, cwd, environment, slave)
        os.close(slave)
        slave = -1
        selector.register(master, selectors.EVENT_READ, "pty")
        selector.register(sys.stdin.buffer, selectors.EVENT_READ, "control")
        _write_message({"type": "ready"})
        while True:
            for key, _ in selector.select(timeout=0.5):
                if key.data == "pty":
                    try:
                        output = os.read(master, 65536)
                    except OSError:
                        output = b""
                    if output:
                        _write_message(
                            {
                                "type": "output",
                                "data": base64.b64encode(output).decode("ascii"),
                            }
                        )
                    continue
                raw_line = sys.stdin.buffer.readline(MAX_CONTROL_LINE_BYTES + 1)
                if not raw_line:
                    process.terminate()
                    break
                try:
                    _handle_control_line(master, raw_line)
                except (ValidationError, ValueError, json.JSONDecodeError):
                    _write_message({"type": "error", "error": "Invalid terminal message"})
            if process.poll() is not None:
                while True:
                    ready = selector.select(timeout=0)
                    if not any(key.data == "pty" for key, _ in ready):
                        break
                    try:
                        output = os.read(master, 65536)
                    except OSError:
                        break
                    if not output:
                        break
                    _write_message(
                        {
                            "type": "output",
                            "data": base64.b64encode(output).decode("ascii"),
                        }
                    )
                break
        return_code = process.wait(timeout=5)
        _write_message({"type": "exit", "exit_code": return_code})
        return 0
    finally:
        selector.close()
        if slave >= 0:
            os.close(slave)
        with contextlib.suppress(OSError):
            os.close(master)
        if process is not None and process.poll() is None:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=3)


def terminal_main() -> int:
    """Read a terminal descriptor and enter the privileged PTY bridge."""
    if os.geteuid() != 0:
        print("Privileged helper must run as root", file=sys.stderr)
        return 2
    raw_line = sys.stdin.buffer.readline(MAX_CONTROL_LINE_BYTES + 1)
    if not raw_line or len(raw_line) > MAX_CONTROL_LINE_BYTES:
        print("Invalid terminal request size", file=sys.stderr)
        return 2
    try:
        request = TerminalOpenRequest.model_validate_json(raw_line)
        return run_terminal(request)
    except (OSError, ValidationError, ValueError, subprocess.SubprocessError) as exc:
        _write_message({"type": "error", "error": str(exc)})
        return 2
