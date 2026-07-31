"""Project CRUD and environment variable endpoints."""

import hashlib
import json
import re

from fastapi import APIRouter, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from app.api.dependencies import AppSettings, CurrentUser, DbSession
from app.core.security import SecretCipher
from app.models.entities import (
    Domain,
    EnvironmentVariable,
    EnvironmentVariableVersion,
    Project,
    ProjectMemberRole,
    ProjectMembership,
    ProjectResourcePolicy,
    ProjectRuntime,
    ProjectSource,
    User,
    UserRole,
)
from app.schemas.common import MessageResponse
from app.schemas.project import (
    EnvironmentImportRequest,
    EnvironmentImportResponse,
    EnvironmentVariableInput,
    EnvironmentVariableResponse,
    EnvironmentVariableRevealRequest,
    EnvironmentVariableRevealResponse,
    EnvironmentVariableRollbackRequest,
    EnvironmentVariableVersionResponse,
    ProjectCreate,
    ProjectDeleteRequest,
    ProjectFromTemplateCreate,
    ProjectGitCredentialInput,
    ProjectGitCredentialResponse,
    ProjectResourcePolicyInput,
    ProjectResourcePolicyResponse,
    ProjectResponse,
    ProjectTemplateResponse,
    ProjectUpdate,
)
from app.schemas.user import ProjectMembershipResponse, ProjectMembershipUpsert
from app.services.agent import AgentClient
from app.services.audit import write_audit_log
from app.services.permissions import (
    accessible_project_ids,
    require_global_role,
    require_project_permission,
)
from app.services.projects import create_project, update_project
from app.services.templates import (
    get_project_template,
    list_project_templates,
    materialize_project_template,
)

router = APIRouter(prefix="/projects", tags=["projects"])
ENV_KEY_PATTERN = re.compile(r"^[A-Z_][A-Z0-9_]{0,254}$")


async def _project_or_404(session: DbSession, project_id: str) -> Project:
    """Load a project or return a stable not-found response."""
    project = await session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


async def _memberships(project_id: str, session: DbSession) -> list[ProjectMembership]:
    """Load project members with attached user records."""
    result = await session.scalars(
        select(ProjectMembership)
        .options(selectinload(ProjectMembership.user))
        .where(ProjectMembership.project_id == project_id)
        .order_by(ProjectMembership.created_at.asc())
    )
    return list(result)


def _environment_response(variable: EnvironmentVariable) -> EnvironmentVariableResponse:
    """Build the masked API response for one environment variable."""
    return EnvironmentVariableResponse(
        id=variable.id,
        key=variable.key,
        value="••••••••" if variable.is_secret else variable.encrypted_value,
        is_secret=variable.is_secret,
        updated_at=variable.updated_at,
    )


def _hash_environment_value(value: str | None) -> str | None:
    """Hash an environment value for comparison without storing plaintext."""
    if value is None:
        return None
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _decrypt_environment_value(
    variable: EnvironmentVariable,
    settings: AppSettings,
) -> str:
    """Return a variable value while respecting encrypted storage."""
    if not variable.is_secret:
        return variable.encrypted_value
    try:
        return SecretCipher(settings.field_encryption_key).decrypt(variable.encrypted_value)
    except ValueError as exc:
        raise HTTPException(
            status_code=409,
            detail="Environment variable value cannot be decrypted",
        ) from exc


def _parse_dotenv(content: str) -> dict[str, str]:
    """Parse a conservative dotenv payload without shell expansion."""
    values: dict[str, str] = {}
    for line_number, raw_line in enumerate(content.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            raise HTTPException(
                status_code=422,
                detail=f"Invalid dotenv line {line_number}",
            )
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not ENV_KEY_PATTERN.fullmatch(key):
            raise HTTPException(
                status_code=422,
                detail=f"Invalid environment key on line {line_number}",
            )
        if key in values:
            raise HTTPException(
                status_code=422,
                detail=f"Duplicate environment key on line {line_number}",
            )
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key] = value
    if not values:
        raise HTTPException(status_code=422, detail="Dotenv content has no variables")
    return values


async def _record_environment_version(
    session: DbSession,
    *,
    project_id: str,
    key: str,
    encrypted_value: str | None,
    is_secret: bool,
    value: str | None,
    action: str,
    actor_id: str | None,
    reason: str | None = None,
    value_sha256: str | None = None,
) -> EnvironmentVariableVersion:
    """Persist an immutable environment variable history row."""
    version = EnvironmentVariableVersion(
        project_id=project_id,
        key=key,
        encrypted_value=encrypted_value,
        is_secret=is_secret,
        value_sha256=_hash_environment_value(value) if value is not None else value_sha256,
        action=action,
        actor_id=actor_id,
        reason=reason,
    )
    session.add(version)
    await session.flush()
    return version


@router.get("", response_model=list[ProjectResponse])
async def list_projects(user: CurrentUser, session: DbSession) -> list[Project]:
    """List all projects in deterministic order."""
    query = select(Project).order_by(Project.created_at.desc())
    project_ids = await accessible_project_ids(session, user)
    if project_ids is not None:
        if not project_ids:
            return []
        query = query.where(Project.id.in_(project_ids))
    result = await session.scalars(query)
    return list(result)


@router.get("/templates", response_model=list[ProjectTemplateResponse])
async def project_templates(user: CurrentUser) -> list[dict[str, object]]:
    """List built-in project templates."""
    del user
    return [template.as_response() for template in list_project_templates()]


@router.post("", response_model=ProjectResponse, status_code=status.HTTP_201_CREATED)
async def create_project_endpoint(
    data: ProjectCreate, request: Request, user: CurrentUser, session: DbSession
) -> Project:
    """Create a deployable project."""
    require_global_role(user, UserRole.OWNER, UserRole.ADMIN)
    try:
        project = await create_project(session, data)
        await write_audit_log(
            session,
            action="project.create",
            actor_id=user.id,
            resource_type="project",
            resource_id=project.id,
            ip_address=request.client.host if request.client else None,
            details={"name": project.name, "runtime": project.runtime_type},
        )
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status_code=409, detail="Project name already exists") from exc
    await session.refresh(project)
    return project


@router.post(
    "/from-template",
    response_model=ProjectResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_project_from_template(
    data: ProjectFromTemplateCreate,
    request: Request,
    user: CurrentUser,
    session: DbSession,
    settings: AppSettings,
) -> Project:
    """Create a project from a built-in template and starter files."""
    require_global_role(user, UserRole.OWNER, UserRole.ADMIN)
    template = get_project_template(data.template_id)
    if template is None:
        raise HTTPException(status_code=404, detail="Project template not found")
    payload = ProjectCreate(
        name=data.name,
        description=data.description or template.description,
        project_type=template.project_type,
        runtime_type=template.runtime_type,
        repository_url=data.repository_url,
        branch=data.branch,
        install_command=template.install_command,
        build_command=template.build_command,
        start_command=template.start_command,
        output_directory=template.output_directory,
        healthcheck_url=template.healthcheck_url,
        runtime_config=template.runtime_config,
    )
    try:
        project = await create_project(session, payload)
        materialize_project_template(settings.storage_root, project, template)
        await write_audit_log(
            session,
            action="project.create_from_template",
            actor_id=user.id,
            resource_type="project",
            resource_id=project.id,
            ip_address=request.client.host if request.client else None,
            details={"name": project.name, "template": template.id},
        )
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status_code=409, detail="Project name already exists") from exc
    except ValueError as exc:
        await session.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await session.refresh(project)
    return project


@router.get("/{project_id}", response_model=ProjectResponse)
async def get_project(project_id: str, user: CurrentUser, session: DbSession) -> Project:
    """Return one project."""
    return await require_project_permission(session, user, project_id, "project:read")


@router.patch("/{project_id}", response_model=ProjectResponse)
async def patch_project(
    project_id: str,
    data: ProjectUpdate,
    user: CurrentUser,
    session: DbSession,
) -> Project:
    """Update mutable project settings."""
    project = await require_project_permission(session, user, project_id, "project:update")
    update_project(project, data)
    source = await session.scalar(
        select(ProjectSource).where(ProjectSource.project_id == project.id)
    )
    if source is not None:
        source.repository_url = project.repository_url
        source.branch = project.branch
        source.source_type = "git" if project.repository_url else "files"
    runtime = await session.scalar(
        select(ProjectRuntime).where(ProjectRuntime.project_id == project.id)
    )
    if runtime is not None:
        runtime.runtime_type = project.runtime_type
        runtime.configuration = project.runtime_config
    await write_audit_log(
        session,
        action="project.update",
        actor_id=user.id,
        resource_type="project",
        resource_id=project.id,
        details={"fields": sorted(data.model_fields_set)},
    )
    await session.commit()
    await session.refresh(project)
    return project


@router.get(
    "/{project_id}/source/credential",
    response_model=ProjectGitCredentialResponse,
)
async def get_project_git_credential(
    project_id: str,
    user: CurrentUser,
    session: DbSession,
    settings: AppSettings,
) -> ProjectGitCredentialResponse:
    """Return masked private Git repository credential status."""
    project = await require_project_permission(session, user, project_id, "project:read")
    source = await session.scalar(
        select(ProjectSource).where(ProjectSource.project_id == project.id)
    )
    if source is None or not source.encrypted_credential:
        return ProjectGitCredentialResponse(configured=False)
    try:
        payload = json.loads(
            SecretCipher(settings.field_encryption_key).decrypt(source.encrypted_credential)
        )
    except (TypeError, ValueError, json.JSONDecodeError):
        return ProjectGitCredentialResponse(configured=True)
    credential_type = payload.get("type") if isinstance(payload, dict) else None
    username = payload.get("username") if isinstance(payload, dict) else None
    return ProjectGitCredentialResponse(
        configured=True,
        credential_type=credential_type if isinstance(credential_type, str) else None,
        username=username if credential_type == "token" and isinstance(username, str) else None,
    )


@router.put(
    "/{project_id}/source/credential",
    response_model=ProjectGitCredentialResponse,
)
async def set_project_git_credential(
    project_id: str,
    data: ProjectGitCredentialInput,
    user: CurrentUser,
    session: DbSession,
    settings: AppSettings,
) -> ProjectGitCredentialResponse:
    """Store an encrypted private Git repository credential."""
    project = await require_project_permission(session, user, project_id, "project:update")
    if not project.repository_url:
        raise HTTPException(status_code=409, detail="Project has no Git repository")
    if data.credential_type == "deploy_key" and "PRIVATE KEY" not in data.value:
        raise HTTPException(status_code=422, detail="Deploy key must be a private key")
    source = await session.scalar(
        select(ProjectSource).where(ProjectSource.project_id == project.id)
    )
    if source is None:
        source = ProjectSource(
            project_id=project.id,
            source_type="git",
            repository_url=project.repository_url,
            branch=project.branch,
        )
        session.add(source)
    payload = {
        "type": data.credential_type,
        "value": data.value,
        "username": data.username or "x-access-token",
    }
    cipher = SecretCipher(settings.field_encryption_key)
    source.encrypted_credential = cipher.encrypt(json.dumps(payload, separators=(",", ":")))
    await write_audit_log(
        session,
        action="project.git_credential.set",
        actor_id=user.id,
        resource_type="project",
        resource_id=project.id,
        details={"credential_type": data.credential_type},
    )
    await session.commit()
    return ProjectGitCredentialResponse(
        configured=True,
        credential_type=data.credential_type,
        username=payload["username"] if data.credential_type == "token" else None,
    )


@router.delete(
    "/{project_id}/source/credential",
    response_model=ProjectGitCredentialResponse,
)
async def delete_project_git_credential(
    project_id: str,
    user: CurrentUser,
    session: DbSession,
) -> ProjectGitCredentialResponse:
    """Remove a stored private Git repository credential."""
    project = await require_project_permission(session, user, project_id, "project:update")
    source = await session.scalar(
        select(ProjectSource).where(ProjectSource.project_id == project.id)
    )
    if source is not None:
        source.encrypted_credential = None
    await write_audit_log(
        session,
        action="project.git_credential.delete",
        actor_id=user.id,
        resource_type="project",
        resource_id=project.id,
    )
    await session.commit()
    return ProjectGitCredentialResponse(configured=False)


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_project(
    project_id: str,
    data: ProjectDeleteRequest,
    request: Request,
    user: CurrentUser,
    session: DbSession,
    settings: AppSettings,
) -> Response:
    """Delete project resources and metadata after exact-name confirmation."""
    require_global_role(user, UserRole.OWNER, UserRole.ADMIN)
    project = await _project_or_404(session, project_id)
    if data.confirm_project_name != project.name:
        raise HTTPException(status_code=409, detail="Project name confirmation does not match")
    hostnames = list(
        await session.scalars(
            select(Domain.hostname)
            .where(Domain.project_id == project.id)
            .order_by(Domain.hostname)
        )
    )
    try:
        await AgentClient(settings).execute(
            "cleanup_project",
            {
                "project_id": project.id,
                "runtime_type": project.runtime_type,
                "hostnames": hostnames,
            },
            request_timeout=900,
        )
    except Exception as exc:
        await write_audit_log(
            session,
            action="project.delete_failed",
            actor_id=user.id,
            resource_type="project",
            resource_id=project.id,
            ip_address=request.client.host if request.client else None,
            details={"name": project.name},
        )
        await session.commit()
        raise HTTPException(
            status_code=502,
            detail="System agent could not clean up project resources",
        ) from exc
    await write_audit_log(
        session,
        action="project.delete",
        actor_id=user.id,
        resource_type="project",
        resource_id=project.id,
        ip_address=request.client.host if request.client else None,
        details={"name": project.name},
    )
    await session.delete(project)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{project_id}/members", response_model=list[ProjectMembershipResponse])
async def list_project_members(
    project_id: str,
    user: CurrentUser,
    session: DbSession,
) -> list[ProjectMembership]:
    """List project-scoped members."""
    require_global_role(user, UserRole.OWNER, UserRole.ADMIN)
    await _project_or_404(session, project_id)
    return await _memberships(project_id, session)


@router.put(
    "/{project_id}/members/{user_id}",
    response_model=ProjectMembershipResponse,
)
async def upsert_project_member(
    project_id: str,
    user_id: str,
    data: ProjectMembershipUpsert,
    request: Request,
    user: CurrentUser,
    session: DbSession,
) -> ProjectMembership:
    """Create or update one project-scoped membership."""
    require_global_role(user, UserRole.OWNER, UserRole.ADMIN)
    await _project_or_404(session, project_id)
    target_user = await session.get(User, user_id)
    if target_user is None or not target_user.is_active:
        raise HTTPException(status_code=404, detail="User not found")
    if target_user.role in {UserRole.OWNER, UserRole.ADMIN}:
        raise HTTPException(status_code=409, detail="Global users do not need memberships")
    membership = await session.scalar(
        select(ProjectMembership).where(
            ProjectMembership.project_id == project_id,
            ProjectMembership.user_id == user_id,
        )
    )
    if membership is None:
        membership = ProjectMembership(
            project_id=project_id,
            user_id=user_id,
            role=str(ProjectMemberRole(data.role)),
        )
        session.add(membership)
        action = "project.member_add"
    else:
        membership.role = str(ProjectMemberRole(data.role))
        action = "project.member_update"
    await write_audit_log(
        session,
        action=action,
        actor_id=user.id,
        resource_type="project",
        resource_id=project_id,
        ip_address=request.client.host if request.client else None,
        details={"user_id": user_id, "role": membership.role},
    )
    await session.commit()
    result = await session.scalar(
        select(ProjectMembership)
        .options(selectinload(ProjectMembership.user))
        .where(ProjectMembership.id == membership.id)
    )
    if result is None:
        raise HTTPException(status_code=404, detail="Project member not found")
    return result


@router.delete("/{project_id}/members/{user_id}", response_model=MessageResponse)
async def delete_project_member(
    project_id: str,
    user_id: str,
    request: Request,
    user: CurrentUser,
    session: DbSession,
) -> MessageResponse:
    """Remove one project-scoped membership."""
    require_global_role(user, UserRole.OWNER, UserRole.ADMIN)
    await _project_or_404(session, project_id)
    membership = await session.scalar(
        select(ProjectMembership).where(
            ProjectMembership.project_id == project_id,
            ProjectMembership.user_id == user_id,
        )
    )
    if membership is None:
        raise HTTPException(status_code=404, detail="Project member not found")
    await session.delete(membership)
    await write_audit_log(
        session,
        action="project.member_delete",
        actor_id=user.id,
        resource_type="project",
        resource_id=project_id,
        ip_address=request.client.host if request.client else None,
        details={"user_id": user_id},
    )
    await session.commit()
    return MessageResponse(message="Project member removed")


@router.get(
    "/{project_id}/resource-policy",
    response_model=ProjectResourcePolicyResponse,
)
async def get_project_resource_policy(
    project_id: str,
    user: CurrentUser,
    session: DbSession,
) -> ProjectResourcePolicyResponse:
    """Return the configured project resource policy or a disabled default."""
    await require_project_permission(session, user, project_id, "project:read")
    policy = await session.scalar(
        select(ProjectResourcePolicy).where(ProjectResourcePolicy.project_id == project_id)
    )
    if policy is None:
        return ProjectResourcePolicyResponse(
            id=None,
            project_id=project_id,
            enabled=False,
            cpu_cores=None,
            memory_mb=None,
            disk_mb=None,
            created_at=None,
            updated_at=None,
        )
    return ProjectResourcePolicyResponse.model_validate(policy)


@router.put(
    "/{project_id}/resource-policy",
    response_model=ProjectResourcePolicyResponse,
)
async def put_project_resource_policy(
    project_id: str,
    data: ProjectResourcePolicyInput,
    request: Request,
    user: CurrentUser,
    session: DbSession,
) -> ProjectResourcePolicyResponse:
    """Create or replace the project resource guardrails policy."""
    require_global_role(user, UserRole.OWNER, UserRole.ADMIN)
    await _project_or_404(session, project_id)
    policy = await session.scalar(
        select(ProjectResourcePolicy).where(ProjectResourcePolicy.project_id == project_id)
    )
    if policy is None:
        policy = ProjectResourcePolicy(project_id=project_id)
        session.add(policy)
    policy.enabled = data.enabled
    policy.cpu_cores = data.cpu_cores
    policy.memory_mb = data.memory_mb
    policy.disk_mb = data.disk_mb
    await write_audit_log(
        session,
        action="project.resource_policy.update",
        actor_id=user.id,
        resource_type="project",
        resource_id=project_id,
        ip_address=request.client.host if request.client else None,
        details={
            "enabled": data.enabled,
            "cpu_cores": data.cpu_cores,
            "memory_mb": data.memory_mb,
            "disk_mb": data.disk_mb,
        },
    )
    await session.commit()
    await session.refresh(policy)
    return ProjectResourcePolicyResponse.model_validate(policy)


@router.get("/{project_id}/env", response_model=list[EnvironmentVariableResponse])
async def list_environment(
    project_id: str, user: CurrentUser, session: DbSession
) -> list[EnvironmentVariableResponse]:
    """List environment variable keys with masked secret values."""
    await require_project_permission(session, user, project_id, "env:read")
    variables = await session.scalars(
        select(EnvironmentVariable)
        .where(EnvironmentVariable.project_id == project_id)
        .order_by(EnvironmentVariable.key)
    )
    return [_environment_response(variable) for variable in variables]


@router.get(
    "/{project_id}/env/{key}/versions",
    response_model=list[EnvironmentVariableVersionResponse],
)
async def list_environment_versions(
    project_id: str,
    key: str,
    user: CurrentUser,
    session: DbSession,
) -> list[EnvironmentVariableVersion]:
    """List safe history entries for one environment variable."""
    if not ENV_KEY_PATTERN.fullmatch(key):
        raise HTTPException(status_code=422, detail="Invalid environment key")
    await require_project_permission(session, user, project_id, "env:read")
    versions = await session.scalars(
        select(EnvironmentVariableVersion)
        .where(
            EnvironmentVariableVersion.project_id == project_id,
            EnvironmentVariableVersion.key == key,
        )
        .order_by(EnvironmentVariableVersion.created_at.desc())
    )
    return list(versions)


@router.post(
    "/{project_id}/env/{key}/reveal",
    response_model=EnvironmentVariableRevealResponse,
)
async def reveal_environment(
    project_id: str,
    key: str,
    data: EnvironmentVariableRevealRequest,
    user: CurrentUser,
    session: DbSession,
    settings: AppSettings,
) -> EnvironmentVariableRevealResponse:
    """Reveal one environment variable after exact-key confirmation."""
    await require_project_permission(session, user, project_id, "env:reveal")
    if key != data.confirm_key:
        raise HTTPException(
            status_code=409,
            detail="Environment key confirmation does not match",
        )
    variable = await session.scalar(
        select(EnvironmentVariable).where(
            EnvironmentVariable.project_id == project_id,
            EnvironmentVariable.key == key,
        )
    )
    if variable is None:
        raise HTTPException(status_code=404, detail="Environment variable not found")
    value = _decrypt_environment_value(variable, settings)
    await _record_environment_version(
        session,
        project_id=project_id,
        key=key,
        encrypted_value=variable.encrypted_value,
        is_secret=variable.is_secret,
        value=value,
        action="reveal",
        actor_id=user.id,
        reason=data.reason,
    )
    await write_audit_log(
        session,
        action="project.env_reveal",
        actor_id=user.id,
        resource_type="project",
        resource_id=project_id,
        details={"key": key, "secret": variable.is_secret},
    )
    await session.commit()
    return EnvironmentVariableRevealResponse(
        key=variable.key,
        value=value,
        is_secret=variable.is_secret,
    )


@router.post(
    "/{project_id}/env/{key}/rollback",
    response_model=EnvironmentVariableResponse,
)
async def rollback_environment(
    project_id: str,
    key: str,
    data: EnvironmentVariableRollbackRequest,
    user: CurrentUser,
    session: DbSession,
) -> EnvironmentVariableResponse:
    """Restore one environment variable from a selected history entry."""
    await require_project_permission(session, user, project_id, "env:write")
    if key != data.confirm_key:
        raise HTTPException(
            status_code=409,
            detail="Environment key confirmation does not match",
        )
    version = await session.scalar(
        select(EnvironmentVariableVersion).where(
            EnvironmentVariableVersion.id == data.version_id,
            EnvironmentVariableVersion.project_id == project_id,
            EnvironmentVariableVersion.key == key,
        )
    )
    if version is None:
        raise HTTPException(status_code=404, detail="Environment version not found")
    if version.encrypted_value is None:
        raise HTTPException(status_code=409, detail="Deleted versions cannot be restored")
    variable = await session.scalar(
        select(EnvironmentVariable).where(
            EnvironmentVariable.project_id == project_id,
            EnvironmentVariable.key == key,
        )
    )
    if variable is None:
        variable = EnvironmentVariable(
            project_id=project_id,
            key=key,
            encrypted_value=version.encrypted_value,
            is_secret=version.is_secret,
        )
        session.add(variable)
    else:
        variable.encrypted_value = version.encrypted_value
        variable.is_secret = version.is_secret
    await _record_environment_version(
        session,
        project_id=project_id,
        key=key,
        encrypted_value=version.encrypted_value,
        is_secret=version.is_secret,
        value=None,
        action="rollback",
        actor_id=user.id,
        reason=data.reason,
        value_sha256=version.value_sha256,
    )
    await write_audit_log(
        session,
        action="project.env_rollback",
        actor_id=user.id,
        resource_type="project",
        resource_id=project_id,
        details={"key": key, "version_id": version.id},
    )
    await session.commit()
    await session.refresh(variable)
    return _environment_response(variable)


@router.post(
    "/{project_id}/env/import",
    response_model=EnvironmentImportResponse,
)
async def import_environment(
    project_id: str,
    data: EnvironmentImportRequest,
    user: CurrentUser,
    session: DbSession,
    settings: AppSettings,
) -> EnvironmentImportResponse:
    """Bulk import dotenv values without shell expansion."""
    await require_project_permission(session, user, project_id, "env:write")
    incoming = _parse_dotenv(data.content)
    current = {
        variable.key: variable
        for variable in await session.scalars(
            select(EnvironmentVariable).where(EnvironmentVariable.project_id == project_id)
        )
    }
    cipher = SecretCipher(settings.field_encryption_key)
    created = 0
    updated = 0
    deleted = 0
    for key, value in incoming.items():
        encrypted_value = cipher.encrypt(value) if data.is_secret else value
        variable = current.get(key)
        if variable is None:
            created += 1
            variable = EnvironmentVariable(
                project_id=project_id,
                key=key,
                encrypted_value=encrypted_value,
                is_secret=data.is_secret,
            )
            session.add(variable)
        else:
            updated += 1
            variable.encrypted_value = encrypted_value
            variable.is_secret = data.is_secret
        await _record_environment_version(
            session,
            project_id=project_id,
            key=key,
            encrypted_value=encrypted_value,
            is_secret=data.is_secret,
            value=value,
            action="import",
            actor_id=user.id,
            reason=data.reason,
        )
    if data.mode == "replace":
        for key, variable in current.items():
            if key in incoming:
                continue
            deleted += 1
            old_value = _decrypt_environment_value(variable, settings)
            await _record_environment_version(
                session,
                project_id=project_id,
                key=key,
                encrypted_value=None,
                is_secret=variable.is_secret,
                value=old_value,
                action="delete",
                actor_id=user.id,
                reason=data.reason,
            )
            await session.delete(variable)
    await write_audit_log(
        session,
        action="project.env_import",
        actor_id=user.id,
        resource_type="project",
        resource_id=project_id,
        details={
            "mode": data.mode,
            "created": created,
            "updated": updated,
            "deleted": deleted,
            "keys": sorted(incoming),
        },
    )
    await session.commit()
    return EnvironmentImportResponse(
        created=created,
        updated=updated,
        deleted=deleted,
        keys=sorted(incoming),
    )


@router.put("/{project_id}/env/{key}", response_model=EnvironmentVariableResponse)
async def put_environment(
    project_id: str,
    key: str,
    data: EnvironmentVariableInput,
    user: CurrentUser,
    session: DbSession,
    settings: AppSettings,
) -> EnvironmentVariableResponse:
    """Create or replace an encrypted environment variable."""
    await require_project_permission(session, user, project_id, "env:write")
    if key != data.key:
        raise HTTPException(status_code=422, detail="Path key must match body key")
    variable = await session.scalar(
        select(EnvironmentVariable).where(
            EnvironmentVariable.project_id == project_id,
            EnvironmentVariable.key == key,
        )
    )
    cipher = SecretCipher(settings.field_encryption_key)
    encrypted_value = cipher.encrypt(data.value) if data.is_secret else data.value
    action = "create" if variable is None else "update"
    if variable is None:
        variable = EnvironmentVariable(
            project_id=project_id,
            key=key,
            encrypted_value=encrypted_value,
            is_secret=data.is_secret,
        )
        session.add(variable)
    else:
        variable.encrypted_value = encrypted_value
        variable.is_secret = data.is_secret
    await _record_environment_version(
        session,
        project_id=project_id,
        key=key,
        encrypted_value=encrypted_value,
        is_secret=data.is_secret,
        value=data.value,
        action=action,
        actor_id=user.id,
        reason=data.reason,
    )
    await write_audit_log(
        session,
        action="project.env_change",
        actor_id=user.id,
        resource_type="project",
        resource_id=project_id,
        details={"key": key, "secret": data.is_secret, "change": action},
    )
    await session.commit()
    await session.refresh(variable)
    return _environment_response(variable)


@router.delete("/{project_id}/env/{key}", response_model=MessageResponse)
async def delete_environment(
    project_id: str,
    key: str,
    user: CurrentUser,
    session: DbSession,
    settings: AppSettings,
) -> MessageResponse:
    """Delete an environment variable without exposing its prior value."""
    await require_project_permission(session, user, project_id, "env:write")
    variable = await session.scalar(
        select(EnvironmentVariable).where(
            EnvironmentVariable.project_id == project_id,
            EnvironmentVariable.key == key,
        )
    )
    if variable is None:
        raise HTTPException(status_code=404, detail="Environment variable not found")
    old_value = _decrypt_environment_value(variable, settings)
    await _record_environment_version(
        session,
        project_id=project_id,
        key=key,
        encrypted_value=None,
        is_secret=variable.is_secret,
        value=old_value,
        action="delete",
        actor_id=user.id,
    )
    await session.delete(variable)
    await write_audit_log(
        session,
        action="project.env_delete",
        actor_id=user.id,
        resource_type="project",
        resource_id=project_id,
        details={"key": key},
    )
    await session.commit()
    return MessageResponse(message="Environment variable deleted")
