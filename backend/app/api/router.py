"""Top-level API router composition."""

from fastapi import APIRouter

from app.api.routes import (
    audit,
    auth,
    backups,
    cron,
    databases,
    domains,
    files,
    jobs,
    minecraft,
    monitoring,
    projects,
    runtime,
    settings,
    sftp,
    system,
    telegram,
    users,
)

api_router = APIRouter()
api_router.include_router(system.router)
api_router.include_router(auth.router)
api_router.include_router(projects.router)
api_router.include_router(domains.router)
api_router.include_router(databases.router)
api_router.include_router(backups.router)
api_router.include_router(cron.router)
api_router.include_router(files.router)
api_router.include_router(runtime.router)
api_router.include_router(minecraft.router)
api_router.include_router(settings.router)
api_router.include_router(sftp.router)
api_router.include_router(telegram.router)
api_router.include_router(users.router)
api_router.include_router(jobs.router)
api_router.include_router(audit.router)
api_router.include_router(monitoring.router)
