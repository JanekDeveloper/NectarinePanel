"""Domain certificate persistence tests."""

from pathlib import Path

import pytest
from app.db.base import Base
from app.models.entities import Domain, Project, SslCertificate
from nectarine_worker import tasks
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


@pytest.mark.asyncio
async def test_worker_persists_certificate_metadata_without_client_polling(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Completed SSL tasks update domain state directly in PostgreSQL-compatible storage."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'ssl.db'}")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with session_factory() as session:
        project = Project(
            name="TLS API",
            slug="tls-api",
            project_type="backend",
            runtime_type="docker",
            status="running",
            branch="main",
            runtime_config={},
        )
        session.add(project)
        await session.flush()
        domain = Domain(
            project_id=project.id,
            hostname="tls.example.com",
            upstream_port=18080,
            is_primary=True,
            ssl_status="queued",
        )
        session.add(domain)
        await session.flush()
        session.add(SslCertificate(domain_id=domain.id, status="queued"))
        await session.commit()
        domain_id = domain.id
    monkeypatch.setattr(tasks, "SessionFactory", session_factory)

    await tasks._store_domain_certificate(
        domain_id,
        "active",
        {
            "expires_at": "2027-06-29T12:00:00+00:00",
            "fingerprint_sha256": "ab" * 32,
        },
    )

    async with session_factory() as session:
        domain = await session.get(Domain, domain_id)
        certificate = await session.scalar(
            select(SslCertificate).where(SslCertificate.domain_id == domain_id)
        )
    await engine.dispose()
    assert domain is not None
    assert domain.ssl_status == "active"
    assert domain.certificate_expires_at is not None
    assert certificate is not None
    assert certificate.fingerprint_sha256 == "ab" * 32
