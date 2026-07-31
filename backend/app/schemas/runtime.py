"""Project runtime, logs, and console schemas."""

from enum import StrEnum

from pydantic import BaseModel, Field


class RuntimeAction(StrEnum):
    """Supported project lifecycle actions."""

    START = "start"
    STOP = "stop"
    RESTART = "restart"


class ConsoleCommand(BaseModel):
    """Command executed inside a project runtime."""

    command: str = Field(min_length=1, max_length=4096)


class ConsoleResult(BaseModel):
    """Bounded command output."""

    exit_code: int
    stdout: str
    stderr: str


class LogResponse(BaseModel):
    """Bounded runtime log snapshot."""

    content: str
