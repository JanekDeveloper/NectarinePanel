"""Allowlisted operation dispatch shared across privilege boundaries."""

from pathlib import Path
from typing import Any

from system_agent import minecraft, operations
from system_agent.config import settings
from system_agent.protocol import Operation, OperationRequest


async def dispatch_operation(request: OperationRequest) -> dict[str, Any]:
    """Dispatch exactly one validated operation without dynamic command lookup."""
    project_id = request.parameters.get("project_id")
    fenced = request.operation in {
        Operation.START_MINECRAFT,
        Operation.INSTALL_MINECRAFT,
        Operation.MINECRAFT_STOP,
        Operation.MINECRAFT_PUBLISH,
        Operation.MINECRAFT_COMMAND,
        Operation.MINECRAFT_BACKUP,
        Operation.MINECRAFT_PLUGIN,
        Operation.MINECRAFT_FENCE,
    }
    if (
        request.operation == Operation.MINECRAFT_PLUGIN
        and request.parameters.get("action") == "list"
    ):
        fenced = False
    root = request.parameters.get("root")
    if isinstance(root, str):
        path = Path(root)
        if path.parent == settings.storage_root / "minecraft":
            project_id = path.name
            fenced = True
    if fenced:
        if not isinstance(project_id, str):
            raise ValueError("Minecraft project ID is required")
        async with minecraft.mutation_lock(project_id):
            return await _dispatch_operation(request)
    return await _dispatch_operation(request)


async def _dispatch_operation(request: OperationRequest) -> dict[str, Any]:
    """Execute the fixed operation switch after optional helper fencing."""
    match request.operation:
        case Operation.CREATE_DIRECTORY:
            return operations.create_directory(**request.parameters)
        case Operation.REMOVE_DIRECTORY:
            return operations.remove_directory(**request.parameters)
        case Operation.CONFIGURE_PROXY:
            return operations.configure_proxy(**request.parameters)
        case Operation.CONFIGURE_STATIC_SITE:
            return operations.configure_static_site(**request.parameters)
        case Operation.REMOVE_NGINX_CONFIG:
            return operations.remove_nginx_config(**request.parameters)
        case Operation.VALIDATE_NGINX:
            return await operations.validate_nginx()
        case Operation.RELOAD_NGINX:
            return await operations.reload_nginx()
        case Operation.ISSUE_CERTIFICATE:
            return await operations.issue_certificate(**request.parameters)
        case Operation.CERTIFICATE_INFO:
            return await operations.certificate_info(**request.parameters)
        case Operation.RENEW_CERTIFICATE:
            return await operations.renew_certificate(**request.parameters)
        case Operation.REMOVE_CERTIFICATE:
            return await operations.remove_certificate(**request.parameters)
        case Operation.RENEW_CERTIFICATES:
            return await operations.renew_certificates()
        case Operation.MANAGE_SERVICE:
            return await operations.manage_service(**request.parameters)
        case Operation.MANAGE_CONTAINER:
            return await operations.manage_container(**request.parameters)
        case Operation.MANAGE_COMPOSE:
            return await operations.manage_compose(**request.parameters)
        case Operation.DEPLOY_CONTAINER:
            return await operations.deploy_container(**request.parameters)
        case Operation.DEPLOY_COMPOSE:
            return await operations.deploy_compose(**request.parameters)
        case Operation.DEPLOY_SYSTEMD:
            return await operations.deploy_systemd(**request.parameters)
        case Operation.DEPLOY_PM2:
            return await operations.deploy_pm2(**request.parameters)
        case Operation.SERVICE_LOGS:
            return await operations.service_logs(**request.parameters)
        case Operation.CONTAINER_LOGS:
            return await operations.container_logs(**request.parameters)
        case Operation.COMPOSE_LOGS:
            return await operations.compose_logs(**request.parameters)
        case Operation.CONTAINER_COMMAND:
            return await operations.container_command(**request.parameters)
        case Operation.COMPOSE_COMMAND:
            return await operations.compose_command(**request.parameters)
        case Operation.RUN_PROJECT_COMMAND:
            return await operations.run_project_command(**request.parameters)
        case Operation.START_MINECRAFT:
            return await operations.start_minecraft(**request.parameters)
        case Operation.INSTALL_MINECRAFT:
            return await operations.install_minecraft(**request.parameters)
        case Operation.MINECRAFT_COMMAND:
            return await operations.minecraft_command(**request.parameters)
        case Operation.MINECRAFT_BACKUP:
            return await operations.minecraft_backup(**request.parameters)
        case Operation.MINECRAFT_STAGE:
            return await minecraft.stage_server(**request.parameters)
        case Operation.MINECRAFT_PUBLISH:
            return minecraft.publish_server(**request.parameters)
        case Operation.MINECRAFT_CLEANUP:
            return await minecraft.cleanup_stage(**request.parameters)
        case Operation.MINECRAFT_STOP:
            return await minecraft.stop_server(**request.parameters)
        case Operation.MINECRAFT_STATUS:
            return await minecraft.server_status(**request.parameters)
        case Operation.MINECRAFT_PLUGIN:
            return await minecraft.plugin_operation(**request.parameters)
        case Operation.MINECRAFT_FENCE:
            return minecraft.fence_runtime(**request.parameters)
        case Operation.ENABLE_SFTP:
            return await operations.enable_sftp(**request.parameters)
        case Operation.DISABLE_SFTP:
            return await operations.disable_sftp(**request.parameters)
        case Operation.CLEANUP_PROJECT:
            return await operations.cleanup_project(**request.parameters)
        case Operation.HOST_METRICS:
            return operations.host_metrics()
        case Operation.PROJECT_METRICS:
            return await operations.project_metrics(**request.parameters)
        case Operation.DATABASE_SERVER_STATUS:
            return await operations.database_server_status(**request.parameters)
        case Operation.DATABASE_TABLES:
            return await operations.database_tables(**request.parameters)
        case Operation.LIST_DIRECTORY:
            return operations.list_directory(**request.parameters)
        case Operation.STAGE_FILE_DOWNLOAD:
            return operations.stage_file_download(**request.parameters)
        case Operation.PUBLISH_STAGED_UPLOAD:
            return operations.publish_staged_upload(**request.parameters)
        case Operation.ARCHIVE_PATH:
            return operations.archive_path(**request.parameters)
        case Operation.EXTRACT_ARCHIVE:
            return operations.extract_archive(**request.parameters)
        case Operation.DELETE_PATH:
            return operations.delete_path(**request.parameters)
        case Operation.MOVE_PATH:
            return operations.move_path(**request.parameters)
