"""Public liveness and authenticated readiness endpoints."""

from fastapi import APIRouter
from sqlalchemy import text

from app.api.dependencies import CurrentUser, DbSession

router = APIRouter(tags=["system"])


@router.get("/health")
async def health() -> dict[str, str]:
    """Return process liveness."""
    return {"status": "ok"}


@router.get("/ready")
async def ready(user: CurrentUser, session: DbSession) -> dict[str, str]:
    """Verify database readiness for authenticated operators."""
    del user
    await session.execute(text("SELECT 1"))
    return {"status": "ready"}
