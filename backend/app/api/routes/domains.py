"""Project domain, Nginx, and certificate endpoints."""

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.api.dependencies import AppSettings, CurrentUser, DbSession
from app.models.entities import Domain, Job, Project, SslCertificate
from app.schemas.common import MessageResponse
from app.schemas.domain import (
    DomainCreate,
    DomainCreateResponse,
    DomainDeleteRequest,
    DomainRenewRequest,
    DomainResponse,
)
from app.schemas.jobs import JobResponse
from app.services.agent import AgentClient
from app.services.audit import write_audit_log
from app.services.dns import ManualDnsProvider
from app.services.queue import enqueue

router = APIRouter(prefix="/projects/{project_id}/domains", tags=["domains"])


@router.get("", response_model=list[DomainResponse])
async def list_domains(
    project_id: str,
    user: CurrentUser,
    session: DbSession,
) -> list[Domain]:
    """List domains attached to one project."""
    del user
    domains = await session.scalars(
        select(Domain)
        .where(Domain.project_id == project_id)
        .order_by(Domain.is_primary.desc(), Domain.hostname)
    )
    return list(domains)


@router.post(
    "",
    response_model=DomainCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_domain(
    project_id: str,
    data: DomainCreate,
    user: CurrentUser,
    session: DbSession,
    settings: AppSettings,
) -> DomainCreateResponse:
    """Verify DNS and queue an Nginx/SSL configuration job."""
    project = await session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    try:
        verification = await ManualDnsProvider().verify(data.hostname, settings.public_ip)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if not verification.matches_expected_ip:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "Domain does not resolve to the configured public IP",
                "addresses": verification.addresses,
            },
        )
    if data.issue_ssl and not settings.letsencrypt_email:
        raise HTTPException(
            status_code=409,
            detail="LETSENCRYPT_EMAIL is required before issuing SSL",
        )
    static_mode = project.runtime_type == "static"
    if not static_mode and data.upstream_port is None:
        raise HTTPException(status_code=422, detail="Upstream port is required")
    output_directory = project.output_directory or str(
        project.runtime_config.get("output_directory") or "dist"
    )
    domain = Domain(
        project_id=project.id,
        hostname=data.hostname,
        upstream_port=0 if static_mode else data.upstream_port,
        is_primary=data.is_primary,
        ssl_status="queued" if data.issue_ssl else "disabled",
    )
    if data.is_primary:
        existing = await session.scalars(select(Domain).where(Domain.project_id == project.id))
        for item in existing:
            item.is_primary = False
    session.add(domain)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status_code=409, detail="Domain is already attached") from exc
    job = Job(
        kind="domain.configure",
        payload={
            "project_id": project.id,
            "domain_id": domain.id,
            "hostname": domain.hostname,
        },
    )
    session.add(job)
    await session.flush()
    if data.issue_ssl:
        session.add(SslCertificate(domain_id=domain.id, status="queued"))
    await write_audit_log(
        session,
        action="domain.add",
        actor_id=user.id,
        resource_type="domain",
        resource_id=domain.id,
        details={"hostname": domain.hostname, "ssl": data.issue_ssl},
    )
    await session.commit()
    await session.refresh(domain)
    enqueue(
        "domains.configure",
        task_id=job.id,
        kwargs={
            "domain_id": domain.id,
            "hostname": domain.hostname,
            "upstream_port": None if static_mode else domain.upstream_port,
            "static_root": (
                f"projects/{project.id}/current/{output_directory}" if static_mode else None
            ),
            "issue_ssl": data.issue_ssl,
            "email": settings.letsencrypt_email,
        },
    )
    return DomainCreateResponse(
        domain=DomainResponse.model_validate(domain),
        job_id=job.id,
        resolved_addresses=list(verification.addresses),
    )


@router.post(
    "/{domain_id}/renew",
    response_model=JobResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def renew_domain_certificate(
    project_id: str,
    domain_id: str,
    data: DomainRenewRequest,
    user: CurrentUser,
    session: DbSession,
) -> Job:
    """Queue a confirmed forced renewal for one managed certificate."""
    domain = await session.get(Domain, domain_id)
    if domain is None or domain.project_id != project_id:
        raise HTTPException(status_code=404, detail="Domain not found")
    if data.confirm_hostname != domain.hostname:
        raise HTTPException(status_code=409, detail="Domain confirmation does not match")
    if domain.ssl_status == "disabled":
        raise HTTPException(status_code=409, detail="SSL is not enabled for this domain")
    job = Job(
        kind="domain.renew",
        payload={
            "project_id": project_id,
            "domain_id": domain.id,
            "hostname": domain.hostname,
        },
    )
    session.add(job)
    await session.flush()
    domain.ssl_status = "renewing"
    await write_audit_log(
        session,
        action="ssl.renew",
        actor_id=user.id,
        resource_type="domain",
        resource_id=domain.id,
        details={"hostname": domain.hostname},
    )
    await session.commit()
    enqueue(
        "domains.renew_certificate",
        task_id=job.id,
        kwargs={"domain_id": domain.id, "hostname": domain.hostname},
    )
    return job


@router.delete("/{domain_id}", response_model=MessageResponse)
async def delete_domain(
    project_id: str,
    domain_id: str,
    data: DomainDeleteRequest,
    user: CurrentUser,
    session: DbSession,
    settings: AppSettings,
) -> MessageResponse:
    """Remove a virtual host through the agent and delete its metadata."""
    domain = await session.get(Domain, domain_id)
    if domain is None or domain.project_id != project_id:
        raise HTTPException(status_code=404, detail="Domain not found")
    if data.confirm_hostname != domain.hostname:
        raise HTTPException(status_code=409, detail="Domain confirmation does not match")
    client = AgentClient(settings)
    try:
        await client.execute("remove_nginx_config", {"hostname": domain.hostname})
        await client.execute("validate_nginx")
        await client.execute("reload_nginx")
        await client.execute("remove_certificate", {"hostname": domain.hostname})
    except Exception as exc:
        raise HTTPException(
            status_code=502, detail="System agent rejected domain removal"
        ) from exc
    await write_audit_log(
        session,
        action="domain.delete",
        actor_id=user.id,
        resource_type="domain",
        resource_id=domain.id,
        details={"hostname": domain.hostname},
    )
    await session.delete(domain)
    await session.commit()
    return MessageResponse(message="Domain removed")
