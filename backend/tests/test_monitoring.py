"""Monitoring API tests."""

import asyncio
from datetime import UTC, datetime, timedelta

from app.models.entities import MetricSample, Project
from conftest import TestSession
from fastapi.testclient import TestClient


def test_project_metrics_latest_returns_latest_samples_and_nulls(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    """Latest project metrics are keyed by project and include projects without samples."""

    async def seed() -> tuple[str, str]:
        """Create projects and deterministic monitoring samples."""
        async with TestSession() as session:
            api = Project(
                name="API",
                slug="api",
                description=None,
                project_type="backend",
                runtime_type="systemd",
                status="running",
                branch="main",
            )
            web = Project(
                name="Web",
                slug="web",
                description=None,
                project_type="web",
                runtime_type="docker",
                status="stopped",
                branch="main",
            )
            session.add_all([api, web])
            await session.flush()
            now = datetime(2026, 7, 1, 12, 0, tzinfo=UTC)
            session.add_all(
                [
                    MetricSample(
                        project_id=api.id,
                        captured_at=now - timedelta(minutes=5),
                        values={"cpu_percent": 10, "health_status": "old"},
                    ),
                    MetricSample(
                        project_id=api.id,
                        captured_at=now,
                        values={"cpu_percent": 25, "health_status": "healthy"},
                    ),
                ]
            )
            await session.commit()
            return api.id, web.id

    api_id, web_id = asyncio.run(seed())
    response = client.get(
        "/api/v1/monitoring/projects/latest",
        headers=auth_headers,
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload[api_id]["project_id"] == api_id
    assert payload[api_id]["values"]["cpu_percent"] == 25
    assert payload[api_id]["values"]["health_status"] == "healthy"
    assert payload[web_id] is None


def test_project_metrics_latest_requires_auth(client: TestClient) -> None:
    """Latest project metrics cannot be read anonymously."""
    response = client.get("/api/v1/monitoring/projects/latest")
    assert response.status_code == 401
