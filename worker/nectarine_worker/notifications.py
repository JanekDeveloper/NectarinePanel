"""Persistent owner alerts and retryable Telegram delivery."""

import asyncio
import logging
from typing import Any

import httpx
from app.core.config import get_settings
from app.db.session import SessionFactory
from app.models.entities import Notification, Project
from app.services.system_settings import telegram_runtime_settings
from celery.signals import task_failure, task_success
from sqlalchemy import select

from nectarine_worker.celery_app import celery_app

settings = get_settings()
logger = logging.getLogger(__name__)
DEPLOY_TASKS = frozenset(
    {
        "projects.git_deploy",
        "projects.archive_deploy",
        "projects.rollback",
    }
)


class TelegramDeliveryError(RuntimeError):
    """Sanitized retryable Telegram transport failure."""


def enqueue_telegram_alert(title: str, message: str) -> None:
    """Queue Telegram delivery without placing credentials in the payload."""
    try:
        celery_app.send_task(
            "notifications.telegram",
            kwargs={"title": title[:255], "message": message[:3500]},
        )
    except Exception:
        logger.error("Unable to enqueue Telegram owner alert")


async def _telegram_delivery_configuration() -> tuple[str, int | None]:
    """Load effective Telegram credentials from persistent runtime settings."""
    async with SessionFactory() as session:
        runtime = await telegram_runtime_settings(session, settings)
    return runtime.bot_token, runtime.owner_id


def _load_telegram_delivery_configuration() -> tuple[str, int | None]:
    """Resolve Telegram credentials for a synchronous Celery task."""
    try:
        return asyncio.run(_telegram_delivery_configuration())
    except Exception as exc:
        if settings.telegram_bot_token and settings.telegram_owner_id is not None:
            return settings.telegram_bot_token, settings.telegram_owner_id
        raise TelegramDeliveryError("Unable to load Telegram configuration") from exc


@celery_app.task(  # type: ignore[untyped-decorator]
    name="notifications.telegram",
    autoretry_for=(TelegramDeliveryError,),
    retry_backoff=True,
    retry_jitter=True,
    max_retries=5,
)
def deliver_telegram_alert(*, title: str, message: str) -> dict[str, bool]:
    """Deliver one plain-text alert to the configured Telegram owner."""
    bot_token, owner_id = _load_telegram_delivery_configuration()
    if not bot_token or owner_id is None:
        return {"delivered": False}
    text = f"{title.strip()}\n\n{message.strip()}"[:4096]
    try:
        response = httpx.post(
            f"https://api.telegram.org/bot{bot_token}/sendMessage",
            json={
                "chat_id": owner_id,
                "text": text,
                "disable_web_page_preview": True,
            },
            timeout=httpx.Timeout(15, connect=5),
        )
    except httpx.HTTPError:
        raise TelegramDeliveryError("Telegram API request failed") from None
    if response.status_code >= 400:
        raise TelegramDeliveryError(f"Telegram API returned HTTP {response.status_code}")
    try:
        accepted = response.json().get("ok") is True
    except ValueError:
        accepted = False
    if not accepted:
        raise TelegramDeliveryError("Telegram API rejected the alert")
    return {"delivered": True}


async def create_owner_alert(
    *,
    notification_type: str,
    severity: str,
    title: str,
    message: str,
    resource_type: str | None = None,
    resource_id: str | None = None,
    deduplicate: bool = True,
) -> bool:
    """Persist an owner alert and queue Telegram delivery when newly created."""
    async with SessionFactory() as session:
        existing = None
        if deduplicate:
            query = select(Notification.id).where(
                Notification.notification_type == notification_type,
                Notification.read_at.is_(None),
            )
            query = (
                query.where(Notification.resource_id == resource_id)
                if resource_id is not None
                else query.where(Notification.resource_id.is_(None))
            )
            existing = await session.scalar(query)
        if existing is not None:
            return False
        session.add(
            Notification(
                notification_type=notification_type,
                severity=severity,
                title=title[:255],
                message=message,
                resource_type=resource_type,
                resource_id=resource_id,
            )
        )
        await session.commit()
    enqueue_telegram_alert(title, message)
    return True


async def _create_deployment_alert(project_id: str, *, successful: bool) -> None:
    """Create a project deployment alert with a human-readable project name."""
    async with SessionFactory() as session:
        project = await session.get(Project, project_id)
        project_name = project.name if project is not None else project_id
    if successful:
        await create_owner_alert(
            notification_type="deploy.success",
            severity="success",
            title="Деплой завершён",
            message=f"Проект {project_name} успешно развёрнут.",
            resource_type="project",
            resource_id=project_id,
            deduplicate=False,
        )
    else:
        await create_owner_alert(
            notification_type="deploy.failed",
            severity="error",
            title="Ошибка деплоя",
            message=f"Не удалось развернуть проект {project_name}. Проверьте журнал задачи.",
            resource_type="project",
            resource_id=project_id,
            deduplicate=False,
        )


@task_success.connect  # type: ignore[untyped-decorator]
def notify_task_success(sender: Any = None, result: Any = None, **signal: Any) -> None:
    """Create a deploy-success alert from completed Celery tasks."""
    del signal
    if getattr(sender, "name", None) not in DEPLOY_TASKS or not isinstance(result, dict):
        return
    project_id = result.get("project_id")
    if not isinstance(project_id, str):
        return
    try:
        asyncio.run(_create_deployment_alert(project_id, successful=True))
    except Exception:
        logger.error("Unable to persist deploy-success alert")


@task_failure.connect  # type: ignore[untyped-decorator]
def notify_task_failure(
    sender: Any = None,
    task_id: str | None = None,
    exception: BaseException | None = None,
    args: Any = None,
    kwargs: Any = None,
    **signal: Any,
) -> None:
    """Create deploy or SSL alerts from failed Celery tasks."""
    del task_id, exception, args, signal
    task_name = getattr(sender, "name", None)
    task_kwargs = kwargs if isinstance(kwargs, dict) else {}
    try:
        if task_name in DEPLOY_TASKS and isinstance(task_kwargs.get("project_id"), str):
            asyncio.run(
                _create_deployment_alert(
                    task_kwargs["project_id"],
                    successful=False,
                )
            )
        elif task_name in {
            "domains.renew_certificates",
            "domains.renew_certificate",
        }:
            asyncio.run(
                create_owner_alert(
                    notification_type="ssl.renewal_failed",
                    severity="error",
                    title="Ошибка продления SSL",
                    message="Certbot не смог продлить сертификаты. Проверьте журнал worker.",
                    resource_type="ssl",
                )
            )
        elif task_name == "domains.configure" and bool(task_kwargs.get("issue_ssl")):
            asyncio.run(
                create_owner_alert(
                    notification_type="ssl.issue_failed",
                    severity="error",
                    title="Ошибка выпуска SSL",
                    message="Не удалось выпустить сертификат для нового домена.",
                    resource_type="ssl",
                    resource_id=(
                        str(task_kwargs["domain_id"])
                        if isinstance(task_kwargs.get("domain_id"), str)
                        else None
                    ),
                )
            )
    except Exception:
        logger.error("Unable to persist task-failure alert")
