"""Database engine service tests."""

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from app.services import databases


class FakeRedis:
    """Minimal Redis/Valkey status client double."""

    def __init__(self, **kwargs: Any) -> None:
        """Capture connection settings."""
        self.settings = kwargs
        self.closed = False

    async def ping(self) -> bool:
        """Return a successful health response."""
        return True

    async def info(self, section: str) -> dict[str, object]:
        """Return bounded engine information by section."""
        values = {
            "server": {"valkey_version": "8.1.0", "uptime_in_seconds": 120},
            "clients": {"connected_clients": 3},
            "memory": {"used_memory": 4096},
        }
        return values[section]

    async def aclose(self) -> None:
        """Record connection closure."""
        self.closed = True


@pytest.mark.asyncio
async def test_inspect_valkey_returns_bounded_info(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Valkey inspection exposes useful metrics without credentials."""
    clients: list[FakeRedis] = []

    def fake_redis(**kwargs: Any) -> FakeRedis:
        """Create and capture the fake client."""
        client = FakeRedis(**kwargs)
        clients.append(client)
        return client

    monkeypatch.setattr(databases, "Redis", fake_redis)
    result = await databases.inspect_database_server(
        SimpleNamespace(
            engine="valkey",
            host="valkey.internal",
            port=6379,
            admin_username="default",
            tls_enabled=True,
        ),
        "secret",
        tmp_path,
    )
    assert result == {
        "engine": "valkey",
        "status": "ready",
        "version": "8.1.0",
        "details": {
            "uptime_seconds": 120,
            "connected_clients": 3,
            "used_memory_bytes": 4096,
        },
    }
    assert clients[0].settings["password"] == "secret"
    assert clients[0].closed is True


def test_sqlite_database_path_is_stable_and_contained(tmp_path: Path) -> None:
    """SQLite files derive only from canonical UUID identifiers."""
    database_id = "11111111-1111-1111-1111-111111111111"
    assert databases.sqlite_database_path(tmp_path, database_id) == (
        tmp_path / "databases" / "sqlite" / f"{database_id}.sqlite3"
    )
    with pytest.raises(ValueError, match="Invalid SQLite database ID"):
        databases.sqlite_database_path(tmp_path, "../../escape")
