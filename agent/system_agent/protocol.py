"""Validated protocol shared by the agent API and privileged helper."""

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator


class Operation(StrEnum):
    """Operations accepted by the agent."""

    CREATE_DIRECTORY = "create_directory"
    REMOVE_DIRECTORY = "remove_directory"
    CONFIGURE_PROXY = "configure_proxy"
    CONFIGURE_STATIC_SITE = "configure_static_site"
    REMOVE_NGINX_CONFIG = "remove_nginx_config"
    VALIDATE_NGINX = "validate_nginx"
    RELOAD_NGINX = "reload_nginx"
    ISSUE_CERTIFICATE = "issue_certificate"
    CERTIFICATE_INFO = "certificate_info"
    RENEW_CERTIFICATE = "renew_certificate"
    REMOVE_CERTIFICATE = "remove_certificate"
    RENEW_CERTIFICATES = "renew_certificates"
    MANAGE_SERVICE = "manage_service"
    MANAGE_CONTAINER = "manage_container"
    MANAGE_COMPOSE = "manage_compose"
    DEPLOY_CONTAINER = "deploy_container"
    DEPLOY_COMPOSE = "deploy_compose"
    DEPLOY_SYSTEMD = "deploy_systemd"
    DEPLOY_PM2 = "deploy_pm2"
    SERVICE_LOGS = "service_logs"
    CONTAINER_LOGS = "container_logs"
    COMPOSE_LOGS = "compose_logs"
    CONTAINER_COMMAND = "container_command"
    COMPOSE_COMMAND = "compose_command"
    RUN_PROJECT_COMMAND = "run_project_command"
    START_MINECRAFT = "start_minecraft"
    INSTALL_MINECRAFT = "install_minecraft"
    MINECRAFT_COMMAND = "minecraft_command"
    MINECRAFT_BACKUP = "minecraft_backup"
    MINECRAFT_STAGE = "minecraft_stage"
    MINECRAFT_PUBLISH = "minecraft_publish"
    MINECRAFT_CLEANUP = "minecraft_cleanup"
    MINECRAFT_STOP = "minecraft_stop"
    MINECRAFT_STATUS = "minecraft_status"
    MINECRAFT_PLUGIN = "minecraft_plugin"
    MINECRAFT_FENCE = "minecraft_fence"
    ENABLE_SFTP = "enable_sftp"
    DISABLE_SFTP = "disable_sftp"
    CLEANUP_PROJECT = "cleanup_project"
    HOST_METRICS = "host_metrics"
    PROJECT_METRICS = "project_metrics"
    DATABASE_SERVER_STATUS = "database_server_status"
    DATABASE_TABLES = "database_tables"
    LIST_DIRECTORY = "list_directory"
    STAGE_FILE_DOWNLOAD = "stage_file_download"
    PUBLISH_STAGED_UPLOAD = "publish_staged_upload"
    ARCHIVE_PATH = "archive_path"
    EXTRACT_ARCHIVE = "extract_archive"
    DELETE_PATH = "delete_path"
    MOVE_PATH = "move_path"


class OperationRequest(BaseModel):
    """Validated agent operation envelope."""

    operation: Operation
    parameters: dict[str, Any] = Field(default_factory=dict)


class OperationResponse(BaseModel):
    """Agent operation result envelope."""

    operation: Operation
    result: dict[str, Any]


class TerminalRuntime(StrEnum):
    """Runtime types that can expose an interactive project terminal."""

    DOCKER = "docker"
    DOCKER_COMPOSE = "docker_compose"
    SYSTEMD = "systemd"
    PM2 = "pm2"


class TerminalOpenRequest(BaseModel):
    """Validated request for one isolated interactive terminal process."""

    runtime_type: TerminalRuntime
    project_id: str = Field(pattern=r"^[a-f0-9-]{36}$")
    project_root: str | None = Field(default=None, min_length=1, max_length=4096)
    service: str | None = Field(
        default=None,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.@-]{0,127}\.service$",
    )
    compose_service: str | None = Field(
        default=None,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$",
    )
    cols: int = Field(default=120, ge=20, le=400)
    rows: int = Field(default=32, ge=5, le=200)

    @model_validator(mode="after")
    def validate_runtime_fields(self) -> "TerminalOpenRequest":
        """Require the metadata needed by host-backed project terminals."""
        if self.runtime_type in {TerminalRuntime.SYSTEMD, TerminalRuntime.PM2}:
            if self.project_root is None or self.service is None:
                raise ValueError("Systemd terminals require a project root and service")
        return self

    @field_validator("project_root")
    @classmethod
    def reject_control_characters(cls, value: str | None) -> str | None:
        """Reject control characters before a path reaches the privileged helper."""
        if value is not None and any(ord(character) < 32 for character in value):
            raise ValueError("Project root contains control characters")
        return value


class TerminalInputMessage(BaseModel):
    """Bounded terminal input forwarded to the PTY."""

    type: str = Field(pattern="^input$")
    data: str = Field(max_length=90_000)


class TerminalResizeMessage(BaseModel):
    """Bounded terminal resize request forwarded to the PTY."""

    type: str = Field(pattern="^resize$")
    cols: int = Field(ge=20, le=400)
    rows: int = Field(ge=5, le=200)
