"""Project lifecycle, logs, and constrained runtime console endpoints."""

import asyncio
import base64
import binascii
import contextlib
import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query, WebSocket, WebSocketDisconnect
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession
from websockets.asyncio.client import connect as connect_websocket
from websockets.exceptions import ConnectionClosed

from app.api.dependencies import AppSettings, CurrentUser, DbSession
from app.core.config import Settings, get_settings
from app.core.security import decode_access_token
from app.db.session import SessionFactory
from app.models.entities import MINECRAFT_TYPES, Project, User
from app.schemas.common import MessageResponse
from app.schemas.runtime import (
    ConsoleCommand,
    ConsoleResult,
    LogResponse,
    RuntimeAction,
)
from app.services.agent import AgentClient
from app.services.audit import write_audit_log
from app.services.minecraft import control_minecraft_runtime, minecraft_rcon_password
from app.services.minecraft_operations import assert_minecraft_idle, minecraft_action
from app.services.permissions import require_project_permission

router = APIRouter(prefix="/projects/{project_id}", tags=["runtime"])

SYSTEMD_SERVICE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.@-]{0,127}\.service$")
COMPOSE_SERVICE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
TERMINAL_RUNTIME_TYPES = {
    "docker",
    "docker_compose",
    "systemd",
    "pm2",
    *MINECRAFT_TYPES,
}


def _container_name(project_id: str) -> str:
    """Return the fixed container name for a project."""
    return f"vps-project-{project_id}"


def _service_name(project: Project) -> str:
    """Return the configured systemd unit for imported or managed projects."""
    configured = project.runtime_config.get("systemd_unit")
    service = configured if isinstance(configured, str) and configured.strip() else None
    resolved = service or f"vps-project-{project.id}.service"
    if not SYSTEMD_SERVICE_PATTERN.fullmatch(resolved):
        raise HTTPException(status_code=409, detail="Invalid project service name")
    return resolved


def _command_audit_details(command: str) -> dict[str, str]:
    """Describe a command without persisting arguments that may contain secrets."""
    name = command.strip().split(maxsplit=1)[0]
    digest = hashlib.sha256(command.encode("utf-8")).hexdigest()
    return {"command_name": name[:120], "command_sha256": digest}


def _terminal_descriptor(project: Project, settings: Settings) -> dict[str, Any]:
    """Build bounded runtime metadata for the internal agent terminal."""
    descriptor: dict[str, Any] = {
        "runtime_type": project.runtime_type,
        "project_id": project.id,
        "cols": 120,
        "rows": 32,
    }
    if project.runtime_type in {"systemd", "pm2"}:
        source_path = project.runtime_config.get("source_path")
        root = (
            Path(source_path)
            if isinstance(source_path, str) and source_path.strip()
            else settings.storage_root / "projects" / project.id / "current"
        )
        descriptor.update(
            {
                "project_root": str(root),
                "service": _service_name(project),
            }
        )
    if project.runtime_type == "docker_compose":
        compose_service = project.runtime_config.get("compose_service")
        if isinstance(compose_service, str) and COMPOSE_SERVICE_PATTERN.fullmatch(
            compose_service
        ):
            descriptor["compose_service"] = compose_service
    return descriptor


def _validated_terminal_message(raw_message: str) -> str:
    """Validate and normalize one browser terminal input or resize message."""
    if len(raw_message) > 96 * 1024:
        raise ValueError("Terminal message is too large")
    payload = json.loads(raw_message)
    if not isinstance(payload, dict):
        raise ValueError("Invalid terminal message")
    message_type = payload.get("type")
    if message_type == "input":
        data = payload.get("data")
        if not isinstance(data, str) or len(data) > 90_000:
            raise ValueError("Invalid terminal input")
        try:
            decoded = base64.b64decode(data, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("Invalid terminal input") from exc
        if len(decoded) > 64 * 1024:
            raise ValueError("Terminal input is too large")
        return json.dumps({"type": "input", "data": data}, separators=(",", ":"))
    if message_type == "resize":
        cols = payload.get("cols")
        rows = payload.get("rows")
        if (
            not isinstance(cols, int)
            or isinstance(cols, bool)
            or not 20 <= cols <= 400
            or not isinstance(rows, int)
            or isinstance(rows, bool)
            or not 5 <= rows <= 200
        ):
            raise ValueError("Invalid terminal size")
        return json.dumps(
            {"type": "resize", "cols": cols, "rows": rows},
            separators=(",", ":"),
        )
    raise ValueError("Unsupported terminal message")


async def _runtime_project_or_409(project_id: str, session: DbSession) -> Project:
    """Load a project with an agent-managed runtime or reject unsupported actions."""
    project = await session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    if project.runtime_type not in {
        "docker",
        "docker_compose",
        "systemd",
        "pm2",
        *MINECRAFT_TYPES,
    }:
        raise HTTPException(
            status_code=409,
            detail="Runtime action requires an agent-managed project",
        )
    return project


@router.post("/runtime/{action}", response_model=MessageResponse)
async def runtime_action(
    project_id: str,
    action: RuntimeAction,
    user: CurrentUser,
    session: DbSession,
    settings: AppSettings,
) -> MessageResponse:
    """Start, stop, or restart a project container through the agent."""
    await require_project_permission(session, user, project_id, "runtime:control")
    project = await _runtime_project_or_409(project_id, session)
    try:
        if project.runtime_type in MINECRAFT_TYPES:
            async with minecraft_action(session, project, str(action)):
                await control_minecraft_runtime(project, str(action), session, settings)
        elif project.runtime_type in {"systemd", "pm2"}:
            await AgentClient(settings).execute(
                "manage_service",
                {
                    "service": _service_name(project),
                    "action": action,
                },
                request_timeout=150,
            )
        elif project.runtime_type == "docker_compose":
            await AgentClient(settings).execute(
                "manage_compose",
                {
                    "project_id": project.id,
                    "action": action,
                },
                request_timeout=150,
            )
        else:
            await AgentClient(settings).execute(
                "manage_container",
                {"container": _container_name(project.id), "action": action},
                request_timeout=150,
            )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail="Runtime operation failed") from exc
    project.status = "stopped" if action == RuntimeAction.STOP else "running"
    await write_audit_log(
        session,
        action=f"project.{action}",
        actor_id=user.id,
        resource_type="project",
        resource_id=project.id,
    )
    await session.commit()
    return MessageResponse(message=f"Project {action} completed")


@router.get("/logs", response_model=LogResponse)
async def project_logs(
    project_id: str,
    user: CurrentUser,
    session: DbSession,
    settings: AppSettings,
    lines: int = Query(default=500, ge=1, le=5000),
) -> LogResponse:
    """Return a bounded Docker log tail."""
    await require_project_permission(session, user, project_id, "logs:read")
    project = await _runtime_project_or_409(project_id, session)
    try:
        if project.runtime_type in {"systemd", "pm2"}:
            result = await AgentClient(settings).execute(
                "service_logs",
                {"service": _service_name(project), "lines": lines},
            )
        elif project.runtime_type == "docker_compose":
            result = await AgentClient(settings).execute(
                "compose_logs",
                {"project_id": project.id, "lines": lines},
            )
        else:
            result = await AgentClient(settings).execute(
                "container_logs",
                {"container": _container_name(project.id), "lines": lines},
            )
    except Exception as exc:
        raise HTTPException(status_code=502, detail="Unable to read project logs") from exc
    content = f"{result.get('stdout', '')}{result.get('stderr', '')}"
    return LogResponse(content=content[-262144:])


@router.post("/console", response_model=ConsoleResult)
async def project_console(
    project_id: str,
    data: ConsoleCommand,
    user: CurrentUser,
    session: DbSession,
    settings: AppSettings,
) -> ConsoleResult:
    """Execute an audited shell command inside the project container."""
    await require_project_permission(session, user, project_id, "console:write")
    project = await _runtime_project_or_409(project_id, session)
    return await _execute_console_command(
        project,
        data.command,
        actor_id=user.id,
        session=session,
        settings=settings,
    )


async def _execute_console_command(
    project: Project,
    command_value: str,
    *,
    actor_id: str,
    session: AsyncSession,
    settings: Settings,
) -> ConsoleResult:
    """Execute one constrained runtime command and persist its audit result."""
    await assert_minecraft_idle(session, project)
    try:
        if project.runtime_type in MINECRAFT_TYPES:
            password = await minecraft_rcon_password(session, project.id, settings)
            result = await AgentClient(settings).execute(
                "minecraft_command",
                {
                    "project_id": project.id,
                    "rcon_host_port": int(project.runtime_config.get("rcon_host_port", 25575)),
                    "rcon_password": password,
                    "command": command_value,
                },
                request_timeout=30,
            )
        elif project.runtime_type in {"systemd", "pm2"}:
            command = command_value.strip()
            if command in {"start", "stop", "restart", "status"}:
                result = await AgentClient(settings).execute(
                    "manage_service",
                    {"service": _service_name(project), "action": command},
                    request_timeout=30,
                )
            elif command == "logs":
                result = await AgentClient(settings).execute(
                    "service_logs",
                    {"service": _service_name(project), "lines": 200},
                    request_timeout=30,
                )
            else:
                raise HTTPException(
                    status_code=409,
                    detail="Systemd/PM2 console supports status, logs, start, stop and restart",
                )
        elif project.runtime_type == "docker_compose":
            command = command_value.strip()
            if command == "ps":
                result = await AgentClient(settings).execute(
                    "manage_compose",
                    {"project_id": project.id, "action": "ps"},
                    request_timeout=30,
                )
            elif command == "logs":
                result = await AgentClient(settings).execute(
                    "compose_logs",
                    {"project_id": project.id, "lines": 200},
                    request_timeout=30,
                )
            else:
                raise HTTPException(
                    status_code=409,
                    detail="Docker Compose console supports only ps and logs",
                )
        else:
            result = await AgentClient(settings).execute(
                "container_command",
                {"container": _container_name(project.id), "command": command_value},
                request_timeout=150,
            )
    except HTTPException:
        await write_audit_log(
            session,
            action="console.command_rejected",
            actor_id=actor_id,
            resource_type="project",
            resource_id=project.id,
            details=_command_audit_details(command_value),
        )
        await session.commit()
        raise
    except Exception as exc:
        await write_audit_log(
            session,
            action="console.command_failed",
            actor_id=actor_id,
            resource_type="project",
            resource_id=project.id,
            details=_command_audit_details(command_value),
        )
        await session.commit()
        raise HTTPException(status_code=502, detail="Container command failed") from exc
    await write_audit_log(
        session,
        action="console.command",
        actor_id=actor_id,
        resource_type="project",
        resource_id=project.id,
        details={
            **_command_audit_details(command_value),
            "exit_code": result["exit_code"],
        },
    )
    await session.commit()
    return ConsoleResult.model_validate(result)


async def _browser_to_agent_terminal(
    websocket: WebSocket,
    agent_socket: Any,
    counters: dict[str, int],
) -> None:
    """Forward validated browser input to the internal agent terminal."""
    while True:
        raw_message = await websocket.receive_text()
        try:
            message = _validated_terminal_message(raw_message)
        except (ValueError, json.JSONDecodeError):
            await websocket.send_json(
                {"type": "error", "error": "Некорректное сообщение терминала."}
            )
            continue
        counters["input_bytes"] += len(message.encode())
        counters["input_messages"] += 1
        await agent_socket.send(message)


async def _agent_to_browser_terminal(
    websocket: WebSocket,
    agent_socket: Any,
    counters: dict[str, int],
) -> None:
    """Forward bounded agent PTY output to the authenticated browser."""
    async for message in agent_socket:
        if not isinstance(message, str) or len(message) > 128 * 1024:
            continue
        counters["output_bytes"] += len(message.encode())
        await websocket.send_text(message)


async def _audit_terminal_event(
    *,
    action: str,
    actor_id: str,
    project_id: str,
    details: dict[str, Any],
) -> None:
    """Persist one terminal session event without terminal contents."""
    async with SessionFactory() as session:
        await write_audit_log(
            session,
            action=action,
            actor_id=actor_id,
            resource_type="project",
            resource_id=project_id,
            details=details,
        )
        await session.commit()


async def _send_terminal_output(websocket: WebSocket, value: str) -> None:
    """Send one UTF-8 output fragment through the terminal wire protocol."""
    await websocket.send_json(
        {
            "type": "output",
            "data": base64.b64encode(value.encode()).decode("ascii"),
        }
    )


def _minecraft_terminal_output(value: str) -> str:
    """Remove Minecraft formatting codes and normalize terminal line endings."""
    text = re.sub(r"§[0-9a-fk-orx]", "", value, flags=re.IGNORECASE)
    return text.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\r\n")


async def _minecraft_terminal_session(
    websocket: WebSocket,
    *,
    project_id: str,
    actor_id: str,
    settings: Settings,
) -> None:
    """Expose the Minecraft RCON command stream with terminal semantics."""
    command_buffer: list[str] = []
    await websocket.send_json({"type": "ready"})
    await _send_terminal_output(websocket, "\x1b[38;5;244mMinecraft RCON\x1b[0m\r\n> ")
    while True:
        raw_message = await websocket.receive_text()
        try:
            message = json.loads(_validated_terminal_message(raw_message))
        except (ValueError, json.JSONDecodeError):
            await websocket.send_json(
                {"type": "error", "error": "Некорректное сообщение терминала."}
            )
            continue
        if message["type"] == "resize":
            continue
        input_value = base64.b64decode(message["data"], validate=True).decode(
            "utf-8", errors="replace"
        )
        if input_value.startswith("\x1b"):
            continue
        echo_buffer: list[str] = []
        for value in input_value:
            if value in {"\n", "\r"}:
                if echo_buffer:
                    await _send_terminal_output(websocket, "".join(echo_buffer))
                    echo_buffer.clear()
                await _send_terminal_output(websocket, "\r\n")
                command = "".join(command_buffer).strip()
                command_buffer.clear()
                if command:
                    async with SessionFactory() as session:
                        project = await session.get(Project, project_id)
                        if project is None:
                            await websocket.close(code=4404, reason="Project not found")
                            return
                        try:
                            result = await _execute_console_command(
                                project,
                                command,
                                actor_id=actor_id,
                                session=session,
                                settings=settings,
                            )
                            output = f"{result.stdout}{result.stderr}".rstrip()
                        except HTTPException as exc:
                            output = str(exc.detail)
                    if output:
                        await _send_terminal_output(
                            websocket, f"{_minecraft_terminal_output(output)}\r\n"
                        )
                await _send_terminal_output(websocket, "> ")
                continue
            if value in {"\b", "\x7f"}:
                if echo_buffer:
                    await _send_terminal_output(websocket, "".join(echo_buffer))
                    echo_buffer.clear()
                if command_buffer:
                    command_buffer.pop()
                    await _send_terminal_output(websocket, "\b \b")
                continue
            if ord(value) < 32 or len(command_buffer) >= 4096:
                continue
            command_buffer.append(value)
            echo_buffer.append(value)
        if echo_buffer:
            await _send_terminal_output(websocket, "".join(echo_buffer))


@router.websocket("/terminal/live")
async def interactive_project_terminal(
    websocket: WebSocket,
    project_id: str,
    token: str = Query(min_length=20),
) -> None:
    """Bridge an authenticated browser to an isolated project PTY."""
    settings = get_settings()
    try:
        payload = decode_access_token(token, settings)
        actor_id = str(payload["sub"])
    except Exception:
        await websocket.close(code=4401, reason="Unauthorized")
        return
    async with SessionFactory() as session:
        user = await session.get(User, actor_id)
        if user is None or not user.is_active:
            await websocket.close(code=4401, reason="Unauthorized")
            return
        try:
            project = await require_project_permission(
                session,
                user,
                project_id,
                "console:write",
            )
        except HTTPException as exc:
            await websocket.close(code=4403, reason=str(exc.detail))
            return
        if project.runtime_type not in TERMINAL_RUNTIME_TYPES:
            await websocket.close(code=4409, reason="Interactive terminal unavailable")
            return
        descriptor = _terminal_descriptor(project, settings)
        runtime_type = project.runtime_type
    await websocket.accept()
    if runtime_type in MINECRAFT_TYPES:
        started_at = time.monotonic()
        await _audit_terminal_event(
            action="console.terminal_opened",
            actor_id=actor_id,
            project_id=project_id,
            details={"runtime_type": runtime_type},
        )
        try:
            await _minecraft_terminal_session(
                websocket,
                project_id=project_id,
                actor_id=actor_id,
                settings=settings,
            )
        except WebSocketDisconnect:
            pass
        finally:
            await _audit_terminal_event(
                action="console.terminal_closed",
                actor_id=actor_id,
                project_id=project_id,
                details={
                    "runtime_type": runtime_type,
                    "duration_seconds": round(time.monotonic() - started_at, 3),
                },
            )
        return
    counters = {"input_bytes": 0, "input_messages": 0, "output_bytes": 0}
    started_at = time.monotonic()
    opened = False
    agent = AgentClient(settings)
    try:
        async with connect_websocket(
            agent.websocket_url("/v1/terminal"),
            additional_headers=agent.authorization_headers,
            max_size=128 * 1024,
            open_timeout=10,
            close_timeout=5,
            ping_interval=20,
            ping_timeout=20,
        ) as agent_socket:
            await agent_socket.send(json.dumps(descriptor, separators=(",", ":")))
            await _audit_terminal_event(
                action="console.terminal_opened",
                actor_id=actor_id,
                project_id=project_id,
                details={"runtime_type": runtime_type},
            )
            opened = True
            browser_task = asyncio.create_task(
                _browser_to_agent_terminal(websocket, agent_socket, counters)
            )
            agent_task = asyncio.create_task(
                _agent_to_browser_terminal(websocket, agent_socket, counters)
            )
            done, pending = await asyncio.wait(
                {browser_task, agent_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            for task in done:
                task.result()
    except (ConnectionClosed, WebSocketDisconnect):
        pass
    except Exception:
        with contextlib.suppress(RuntimeError, WebSocketDisconnect):
            await websocket.send_json(
                {
                    "type": "error",
                    "error": "Не удалось открыть терминал проекта.",
                }
            )
    finally:
        if opened:
            audit_task = asyncio.create_task(
                _audit_terminal_event(
                    action="console.terminal_closed",
                    actor_id=actor_id,
                    project_id=project_id,
                    details={
                        "runtime_type": runtime_type,
                        "duration_seconds": round(time.monotonic() - started_at, 3),
                        **counters,
                    },
                )
            )
            try:
                await asyncio.shield(audit_task)
            except asyncio.CancelledError:
                with contextlib.suppress(asyncio.CancelledError):
                    await audit_task
                raise
        with contextlib.suppress(RuntimeError):
            await websocket.close()


@router.websocket("/console/live")
async def live_project_console(
    websocket: WebSocket,
    project_id: str,
    token: str = Query(min_length=20),
) -> None:
    """Execute sequential audited console commands over an authenticated WebSocket."""
    settings = get_settings()
    try:
        payload = decode_access_token(token, settings)
        actor_id = str(payload["sub"])
    except Exception:
        await websocket.close(code=4401, reason="Unauthorized")
        return
    async with SessionFactory() as session:
        project = await session.get(Project, project_id)
        if project is None or project.runtime_type not in {
            "docker",
            "docker_compose",
            "systemd",
            "pm2",
            *MINECRAFT_TYPES,
        }:
            await websocket.close(code=4404, reason="Project not found")
            return
        runtime_type = project.runtime_type
    await websocket.accept()
    await websocket.send_json({"type": "ready", "runtime_type": runtime_type})
    command_count = 0
    last_command_at = 0.0
    try:
        while True:
            payload = await websocket.receive_json()
            command_count += 1
            if command_count > 500:
                await websocket.close(code=4429, reason="Command limit exceeded")
                return
            request_id = payload.get("request_id") if isinstance(payload, dict) else None
            try:
                data = ConsoleCommand.model_validate(payload)
            except ValidationError:
                await websocket.send_json(
                    {
                        "type": "error",
                        "request_id": request_id,
                        "error": "Invalid console command",
                    }
                )
                continue
            elapsed = time.monotonic() - last_command_at
            if elapsed < 0.2:
                await asyncio.sleep(0.2 - elapsed)
            last_command_at = time.monotonic()
            async with SessionFactory() as session:
                project = await session.get(Project, project_id)
                if project is None:
                    await websocket.close(code=4404, reason="Project not found")
                    return
                try:
                    result = await _execute_console_command(
                        project,
                        data.command,
                        actor_id=actor_id,
                        session=session,
                        settings=settings,
                    )
                except HTTPException as exc:
                    await websocket.send_json(
                        {
                            "type": "error",
                            "request_id": request_id,
                            "error": str(exc.detail),
                        }
                    )
                    continue
            await websocket.send_json(
                {
                    "type": "result",
                    "request_id": request_id,
                    **result.model_dump(),
                }
            )
    except WebSocketDisconnect:
        return


@router.websocket("/logs/live")
async def live_project_logs(
    websocket: WebSocket,
    project_id: str,
    token: str = Query(min_length=20),
) -> None:
    """Stream changing bounded log snapshots for one Docker project."""
    settings = get_settings()
    try:
        decode_access_token(token, settings)
    except Exception:
        await websocket.close(code=4401, reason="Unauthorized")
        return
    async with SessionFactory() as session:
        project = await session.get(Project, project_id)
        if project is None or project.runtime_type not in {
            "docker",
            "docker_compose",
            "systemd",
            "pm2",
            *MINECRAFT_TYPES,
        }:
            await websocket.close(code=4404, reason="Project not found")
            return
        service = _service_name(project) if project.runtime_type in {"systemd", "pm2"} else None
    await websocket.accept()
    previous = ""
    client = AgentClient(settings)
    try:
        while True:
            if project.runtime_type in {"systemd", "pm2"}:
                result = await client.execute(
                    "service_logs",
                    {"service": service or _service_name(project), "lines": 500},
                )
            elif project.runtime_type == "docker_compose":
                result = await client.execute(
                    "compose_logs",
                    {"project_id": project_id, "lines": 500},
                )
            else:
                result = await client.execute(
                    "container_logs",
                    {"container": _container_name(project_id), "lines": 500},
                )
            content = f"{result.get('stdout', '')}{result.get('stderr', '')}"[-262144:]
            if content != previous:
                await websocket.send_json({"content": content})
                previous = content
            await asyncio.sleep(2)
    except WebSocketDisconnect:
        return
    except Exception:
        return
