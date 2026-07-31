"""FastAPI application entry point."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.router import api_router
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.services.rate_limit import RateLimiter
from app.version import APP_VERSION

settings = get_settings()


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    """Initialize process resources and validate runtime configuration."""
    configure_logging()
    settings.storage_root.mkdir(parents=True, exist_ok=True)
    application.state.rate_limiter = RateLimiter(settings.redis_url)
    try:
        yield
    finally:
        await application.state.rate_limiter.close()


app = FastAPI(
    title=settings.app_name,
    version=APP_VERSION,
    openapi_url=f"{settings.api_prefix}/openapi.json",
    docs_url=f"{settings.api_prefix}/docs",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
)
app.include_router(api_router, prefix=settings.api_prefix)
