"""Tests for administrative CLI services."""

import pytest
from app.cli import create_admin


@pytest.mark.asyncio
async def test_create_admin_is_idempotent_when_requested() -> None:
    """Return false instead of duplicating an existing owner."""
    assert await create_admin("installer-admin", "a-production-password") is True
    assert (
        await create_admin(
            "installer-admin",
            "a-production-password",
            if_not_exists=True,
        )
        is False
    )
