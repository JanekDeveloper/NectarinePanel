"""Celery worker configuration."""

from app.core.config import get_settings
from celery import Celery

settings = get_settings()
celery_app = Celery(
    "nectarine_worker",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["nectarine_worker.tasks"],
)
celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_time_limit=3600,
    task_soft_time_limit=3300,
    worker_prefetch_multiplier=1,
    beat_schedule={
        "collect-host-metrics": {
            "task": "monitoring.collect",
            "schedule": 60.0,
        },
        "run-project-cron": {
            "task": "cron.tick",
            "schedule": 60.0,
        },
        "run-backup-policies": {
            "task": "backups.tick",
            "schedule": 60.0,
        },
        "renew-certificates": {
            "task": "domains.renew_certificates",
            "schedule": 12 * 60 * 60,
        },
    },
)
