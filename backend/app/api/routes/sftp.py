"""Restricted per-project SFTP lifecycle endpoints."""

import hashlib
import secrets

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from app.api.dependencies import AppSettings, CurrentUser, DbSession
from app.models.entities import Project, SftpAccount
from app.schemas.common import MessageResponse
from app.schemas.sftp import SftpAccountResponse, SftpEnableRequest
from app.services.agent import AgentClient
from app.services.audit import write_audit_log

router = APIRouter(prefix="/projects/{project_id}/sftp", tags=["sftp"])


@router.get("", response_model=SftpAccountResponse)
async def sftp_status(
    project_id: str, user: CurrentUser, session: DbSession
) -> SftpAccountResponse:
    """Return SFTP status without credentials."""
    del user
    account = await session.scalar(
        select(SftpAccount).where(SftpAccount.project_id == project_id)
    )
    if account is None:
        return SftpAccountResponse(enabled=False)
    return SftpAccountResponse(enabled=account.enabled, username=account.username)


@router.put("", response_model=SftpAccountResponse)
async def enable_sftp(
    project_id: str,
    data: SftpEnableRequest,
    user: CurrentUser,
    session: DbSession,
    settings: AppSettings,
) -> SftpAccountResponse:
    """Enable or reset a restricted project SFTP account."""
    project = await session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    password = secrets.token_urlsafe(24) if data.generate_password else None
    if password is None and not data.public_key:
        raise HTTPException(status_code=422, detail="Password or public key is required")
    try:
        result = await AgentClient(settings).execute(
            "enable_sftp",
            {
                "project_id": project.id,
                "password": password,
                "public_key": data.public_key,
            },
            request_timeout=60,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail="SFTP configuration failed") from exc
    account = await session.scalar(
        select(SftpAccount).where(SftpAccount.project_id == project.id)
    )
    fingerprint = (
        hashlib.sha256(data.public_key.encode()).hexdigest() if data.public_key else None
    )
    if account is None:
        account = SftpAccount(
            project_id=project.id,
            username=str(result["username"]),
            public_key_fingerprint=fingerprint,
        )
        session.add(account)
    else:
        account.enabled = True
        account.public_key_fingerprint = fingerprint
    await write_audit_log(
        session,
        action="sftp.enable",
        actor_id=user.id,
        resource_type="project",
        resource_id=project.id,
        details={"username": account.username, "key_configured": bool(data.public_key)},
    )
    await session.commit()
    return SftpAccountResponse(
        enabled=True,
        username=account.username,
        directory=str(result["directory"]),
        one_time_password=password,
    )


@router.delete("", response_model=MessageResponse)
async def disable_sftp(
    project_id: str,
    user: CurrentUser,
    session: DbSession,
    settings: AppSettings,
) -> MessageResponse:
    """Disable SFTP while preserving uploaded project files."""
    account = await session.scalar(
        select(SftpAccount).where(SftpAccount.project_id == project_id)
    )
    if account is None:
        raise HTTPException(status_code=404, detail="SFTP is not configured")
    try:
        await AgentClient(settings).execute(
            "disable_sftp",
            {"project_id": project_id},
            request_timeout=60,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail="SFTP disable failed") from exc
    account.enabled = False
    await write_audit_log(
        session,
        action="sftp.disable",
        actor_id=user.id,
        resource_type="project",
        resource_id=project_id,
    )
    await session.commit()
    return MessageResponse(message="SFTP disabled")
