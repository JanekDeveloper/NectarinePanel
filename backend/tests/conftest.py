"""Backend API test fixtures."""

import os
import shutil
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///./test-nectarine.db"
os.environ["JWT_SECRET"] = "test-secret-that-is-at-least-thirty-two-characters"
os.environ["STORAGE_ROOT"] = "/tmp/nectarine-panel-tests"

import pytest
import pytest_asyncio
from app.api.dependencies import get_rate_limiter
from app.core.security import hash_password
from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.models.entities import User
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

TEST_DATABASE_URL = "sqlite+aiosqlite:///./test-nectarine.db"
test_engine = create_async_engine(TEST_DATABASE_URL)
TestSession = async_sessionmaker(test_engine, expire_on_commit=False)


async def override_db() -> AsyncIterator[AsyncSession]:
    """Yield a database session bound to the test engine."""
    async with TestSession() as session:
        yield session


app.dependency_overrides[get_db] = override_db


class AllowAllRateLimiter:
    """Deterministic rate limiter used by API tests."""

    async def check(self, key: str, limit: int, window_seconds: int) -> bool:
        """Allow every test login attempt."""
        del key, limit, window_seconds
        return True


app.dependency_overrides[get_rate_limiter] = lambda: AllowAllRateLimiter()


@pytest_asyncio.fixture(autouse=True)
async def database() -> AsyncIterator[None]:
    """Create an isolated schema for each test."""
    async with test_engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with TestSession() as session:
        session.add(
            User(
                username="admin",
                password_hash=hash_password("correct-horse-battery"),
            )
        )
        await session.commit()
    yield
    async with test_engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)


@pytest.fixture
def client() -> Iterator[TestClient]:
    """Yield an in-process FastAPI client."""
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def auth_headers(client: TestClient) -> dict[str, str]:
    """Authenticate and return bearer headers."""
    response = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "correct-horse-battery"},
    )
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def pytest_sessionfinish() -> None:
    """Remove the temporary SQLite database after the test session."""
    Path("test-nectarine.db").unlink(missing_ok=True)
    shutil.rmtree("/tmp/nectarine-panel-tests", ignore_errors=True)
