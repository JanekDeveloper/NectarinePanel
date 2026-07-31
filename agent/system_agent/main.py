"""Authenticated HTTP and WebSocket interface for allowlisted system operations."""

import asyncio
import contextlib
import json
import secrets
import sys

from fastapi import Depends, FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import ValidationError

from system_agent import operations
from system_agent.config import settings
from system_agent.dispatcher import dispatch_operation
from system_agent.protocol import (
    OperationRequest,
    OperationResponse,
    TerminalInputMessage,
    TerminalOpenRequest,
    TerminalResizeMessage,
)
from system_agent.security import require_agent_token

app = FastAPI(
    title="NectarinePanel System Agent",
    version="0.1.0",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


@app.get("/health")
async def health() -> dict[str, str]:
    """Return unauthenticated liveness without host details."""
    return {"status": "ok"}


async def _execute_operation(request: OperationRequest) -> OperationResponse:
    """Execute directly in development or through the exact production helper."""
    if settings.privileged_helper is None:
        result = await dispatch_operation(request)
        return OperationResponse(operation=request.operation, result=result)
    response = await operations.run_command(
        [
            str(settings.sudo_binary),
            "--non-interactive",
            str(settings.privileged_helper),
        ],
        command_timeout=3600,
        stdin_data=request.model_dump_json().encode(),
        output_limit=1024 * 1024,
    )
    return OperationResponse.model_validate_json(response["stdout"])


@app.post("/v1/operations", response_model=OperationResponse)
async def execute_operation(
    request: OperationRequest, _: None = Depends(require_agent_token)
) -> OperationResponse:
    """Dispatch exactly one allowlisted system operation."""
    try:
        return await _execute_operation(request)
    except (TypeError, ValueError, operations.CommandError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _terminal_helper_arguments() -> list[str]:
    """Return the fixed privileged terminal helper invocation."""
    if settings.privileged_helper is not None:
        return [
            str(settings.sudo_binary),
            "--non-interactive",
            str(settings.privileged_helper),
            "--terminal",
        ]
    return [sys.executable, "-m", "system_agent.privileged_helper", "--terminal"]


def _valid_agent_authorization(websocket: WebSocket) -> bool:
    """Validate the agent bearer token in constant time."""
    authorization = websocket.headers.get("authorization", "")
    prefix = "Bearer "
    supplied = authorization[len(prefix) :] if authorization.startswith(prefix) else ""
    return bool(supplied) and secrets.compare_digest(supplied, settings.agent_token)


async def _forward_terminal_input(
    websocket: WebSocket,
    process: asyncio.subprocess.Process,
) -> None:
    """Validate browser control frames and forward them to the helper."""
    if process.stdin is None:
        raise RuntimeError("Terminal helper stdin is unavailable")
    while True:
        raw_message = await websocket.receive_text()
        if len(raw_message) > 96 * 1024:
            await websocket.close(code=4400, reason="Terminal message too large")
            return
        try:
            payload = json.loads(raw_message)
            if not isinstance(payload, dict):
                raise ValueError
            message = (
                TerminalInputMessage.model_validate(payload)
                if payload.get("type") == "input"
                else TerminalResizeMessage.model_validate(payload)
            )
        except (ValidationError, ValueError, json.JSONDecodeError):
            await websocket.send_json({"type": "error", "error": "Invalid terminal message"})
            continue
        process.stdin.write((message.model_dump_json() + "\n").encode())
        await process.stdin.drain()


async def _forward_terminal_output(
    websocket: WebSocket,
    process: asyncio.subprocess.Process,
) -> None:
    """Forward bounded helper output frames to the backend."""
    if process.stdout is None:
        raise RuntimeError("Terminal helper stdout is unavailable")
    while raw_line := await process.stdout.readline():
        if len(raw_line) > 128 * 1024:
            await websocket.send_json(
                {"type": "error", "error": "Terminal output frame is too large"}
            )
            continue
        await websocket.send_text(raw_line.decode("utf-8", errors="replace").rstrip("\n"))


async def _close_terminal_process(process: asyncio.subprocess.Process) -> None:
    """Close the helper input and stop any orphaned terminal process."""
    if process.stdin is not None:
        process.stdin.close()
        with contextlib.suppress(BrokenPipeError, ConnectionResetError):
            await process.stdin.wait_closed()
    try:
        await asyncio.wait_for(process.wait(), timeout=5)
    except TimeoutError:
        process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=3)
        except TimeoutError:
            process.kill()
            await process.wait()


@app.websocket("/v1/terminal")
async def terminal_session(websocket: WebSocket) -> None:
    """Bridge one authenticated project terminal to the privileged PTY helper."""
    if not _valid_agent_authorization(websocket):
        await websocket.close(code=4401, reason="Invalid agent credential")
        return
    await websocket.accept()
    process: asyncio.subprocess.Process | None = None
    try:
        raw_request = await asyncio.wait_for(websocket.receive_text(), timeout=10)
        request = TerminalOpenRequest.model_validate_json(raw_request)
        process = await asyncio.create_subprocess_exec(
            *_terminal_helper_arguments(),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        if process.stdin is None:
            raise RuntimeError("Terminal helper stdin is unavailable")
        process.stdin.write((request.model_dump_json() + "\n").encode())
        await process.stdin.drain()
        input_task = asyncio.create_task(_forward_terminal_input(websocket, process))
        output_task = asyncio.create_task(_forward_terminal_output(websocket, process))
        done, pending = await asyncio.wait(
            {input_task, output_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        for task in done:
            task.result()
    except (TimeoutError, ValidationError, ValueError):
        await websocket.send_json({"type": "error", "error": "Invalid terminal request"})
    except WebSocketDisconnect:
        pass
    except Exception:
        with contextlib.suppress(RuntimeError, WebSocketDisconnect):
            await websocket.send_json({"type": "error", "error": "Terminal session failed"})
    finally:
        if process is not None:
            await _close_terminal_process(process)
        with contextlib.suppress(RuntimeError):
            await websocket.close()
