"""Domain routing API tests."""

import asyncio
from dataclasses import dataclass

import pytest
from app.models.entities import Domain, SslCertificate
from conftest import TestSession
from fastapi.testclient import TestClient


@dataclass(frozen=True)
class FakeDnsResult:
    """Deterministic DNS verification result."""

    matches_expected_ip: bool = True
    addresses: tuple[str, ...] = ("203.0.113.10",)


def test_static_project_domain_queues_static_nginx_config(
    client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Static projects do not require an upstream port for domain routing."""
    from app.api.routes import domains
    from app.services.dns import ManualDnsProvider

    queued: list[tuple[str, str, dict[str, object]]] = []

    async def fake_verify(
        self: ManualDnsProvider,
        hostname: str,
        expected_ip: str | None,
    ) -> FakeDnsResult:
        """Return a successful DNS check without network access."""
        del self
        del expected_ip
        assert hostname == "static.example.com"
        return FakeDnsResult()

    monkeypatch.setattr(ManualDnsProvider, "verify", fake_verify)
    monkeypatch.setattr(
        domains,
        "enqueue",
        lambda name, task_id, kwargs: queued.append((name, task_id, kwargs)),
    )
    created = client.post(
        "/api/v1/projects",
        headers=auth_headers,
        json={
            "name": "Static Site",
            "project_type": "static",
            "runtime_type": "static",
            "output_directory": "public",
        },
    )
    assert created.status_code == 201
    project_id = created.json()["id"]
    response = client.post(
        f"/api/v1/projects/{project_id}/domains",
        headers=auth_headers,
        json={"hostname": "static.example.com", "issue_ssl": False},
    )
    assert response.status_code == 201
    payload = response.json()
    assert payload["domain"]["upstream_port"] == 0
    assert queued[0][0] == "domains.configure"
    assert queued[0][2]["upstream_port"] is None
    assert queued[0][2]["static_root"] == f"projects/{project_id}/current/public"

    from app.services.agent import AgentClient

    agent_calls: list[tuple[str, dict[str, object] | None]] = []

    async def fake_execute(
        self: AgentClient,
        operation: str,
        parameters: dict[str, object] | None = None,
        *,
        request_timeout: float = 30,
    ) -> dict[str, object]:
        """Record domain cleanup operations without host access."""
        del self, request_timeout
        agent_calls.append((operation, parameters))
        return {}

    monkeypatch.setattr(AgentClient, "execute", fake_execute)
    domain_id = payload["domain"]["id"]
    rejected = client.request(
        "DELETE",
        f"/api/v1/projects/{project_id}/domains/{domain_id}",
        headers=auth_headers,
        json={"confirm_hostname": "wrong.example.com"},
    )
    assert rejected.status_code == 409
    deleted = client.request(
        "DELETE",
        f"/api/v1/projects/{project_id}/domains/{domain_id}",
        headers=auth_headers,
        json={"confirm_hostname": "static.example.com"},
    )
    assert deleted.status_code == 200
    assert agent_calls == [
        ("remove_nginx_config", {"hostname": "static.example.com"}),
        ("validate_nginx", None),
        ("reload_nginx", None),
        ("remove_certificate", {"hostname": "static.example.com"}),
    ]


def test_manual_ssl_renewal_requires_exact_hostname(
    client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Manual renewal is queued only for an exact confirmed managed domain."""
    from app.api.routes import domains

    project = client.post(
        "/api/v1/projects",
        headers=auth_headers,
        json={
            "name": "TLS API",
            "project_type": "backend",
            "runtime_type": "docker",
        },
    ).json()

    async def create_domain() -> str:
        """Create active certificate metadata without external DNS access."""
        async with TestSession() as session:
            domain = Domain(
                project_id=project["id"],
                hostname="tls.example.com",
                upstream_port=18080,
                ssl_status="active",
                is_primary=True,
            )
            session.add(domain)
            await session.flush()
            session.add(SslCertificate(domain_id=domain.id, status="active"))
            await session.commit()
            return domain.id

    domain_id = asyncio.run(create_domain())
    queued: list[tuple[str, str, dict[str, str]]] = []
    monkeypatch.setattr(
        domains,
        "enqueue",
        lambda name, task_id, kwargs: queued.append((name, task_id, kwargs)),
    )
    rejected = client.post(
        f"/api/v1/projects/{project['id']}/domains/{domain_id}/renew",
        headers=auth_headers,
        json={"confirm_hostname": "wrong.example.com"},
    )
    assert rejected.status_code == 409
    accepted = client.post(
        f"/api/v1/projects/{project['id']}/domains/{domain_id}/renew",
        headers=auth_headers,
        json={"confirm_hostname": "tls.example.com"},
    )
    assert accepted.status_code == 202
    assert accepted.json()["kind"] == "domain.renew"
    assert queued == [
        (
            "domains.renew_certificate",
            accepted.json()["id"],
            {"domain_id": domain_id, "hostname": "tls.example.com"},
        )
    ]
