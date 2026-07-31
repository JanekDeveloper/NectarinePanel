"""Agent authentication and filesystem security."""

import secrets
from pathlib import Path

from fastapi import Header, HTTPException, status

from system_agent.config import settings


async def require_agent_token(
    authorization: str | None = Header(default=None),
) -> None:
    """Require a constant-time bearer token match."""
    prefix = "Bearer "
    supplied = (
        authorization[len(prefix) :]
        if authorization and authorization.startswith(prefix)
        else ""
    )
    if not supplied or not secrets.compare_digest(supplied, settings.agent_token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid agent credential",
        )


def safe_child(root: Path, relative: str) -> Path:
    """Resolve a path and reject traversal outside the configured root."""
    root = root.resolve()
    candidate = (root / relative.lstrip("/")).resolve()
    if candidate != root and root not in candidate.parents:
        raise ValueError("Path escapes the configured root")
    return candidate
