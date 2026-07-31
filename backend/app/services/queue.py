"""Celery client for dispatching long-running operations."""

from typing import Any

from celery import Celery
from celery.result import AsyncResult

from app.core.config import get_settings

settings = get_settings()
queue = Celery("nectarine_backend", broker=settings.redis_url, backend=settings.redis_url)


def enqueue(task_name: str, *, task_id: str, kwargs: dict[str, Any]) -> None:
    """Enqueue a named task using an existing database job ID."""
    queue.send_task(task_name, task_id=task_id, kwargs=kwargs)


def task_status(task_id: str) -> dict[str, Any]:
    """Return a normalized Celery task status snapshot."""
    result: AsyncResult = queue.AsyncResult(task_id)
    info = result.info
    payload: dict[str, Any] = {"id": task_id, "state": result.state}
    if isinstance(info, dict):
        payload.update(info)
    elif result.failed():
        payload["error"] = str(info)
    if result.successful():
        payload["result"] = result.result
        payload["progress"] = 100
    return payload
