"""Worker Telegram alert delivery tests."""

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from app.db.base import Base
from app.models.entities import Notification
from nectarine_worker import notifications
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


class FakeTelegramResponse:
    """Minimal Telegram HTTP response double."""

    def __init__(self, status_code: int, payload: dict[str, Any]) -> None:
        """Store deterministic response data."""
        self.status_code = status_code
        self._payload = payload

    def json(self) -> dict[str, Any]:
        """Return the configured JSON object."""
        return self._payload


def test_telegram_delivery_targets_owner_with_plain_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Delivery uses the owner chat and never enables Telegram markup parsing."""
    captured: dict[str, Any] = {}

    def fake_post(url: str, **kwargs: Any) -> FakeTelegramResponse:
        """Capture the Telegram request."""
        captured["url"] = url
        captured.update(kwargs)
        return FakeTelegramResponse(200, {"ok": True})

    monkeypatch.setattr(notifications.settings, "telegram_bot_token", "bot-secret-token")
    monkeypatch.setattr(notifications.settings, "telegram_owner_id", 123456)
    monkeypatch.setattr(notifications.httpx, "post", fake_post)
    result = notifications.deliver_telegram_alert.run(
        title="Deploy",
        message="<b>untrusted</b>",
    )
    assert result == {"delivered": True}
    assert captured["json"] == {
        "chat_id": 123456,
        "text": "Deploy\n\n<b>untrusted</b>",
        "disable_web_page_preview": True,
    }
    assert "parse_mode" not in captured["json"]


def test_telegram_http_failure_is_sanitized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Retry exceptions do not expose the bot credential or Telegram response body."""
    monkeypatch.setattr(notifications.settings, "telegram_bot_token", "bot-secret-token")
    monkeypatch.setattr(notifications.settings, "telegram_owner_id", 123456)
    monkeypatch.setattr(
        notifications.httpx,
        "post",
        lambda *args, **kwargs: FakeTelegramResponse(
            500,
            {"ok": False, "description": "sensitive remote body"},
        ),
    )
    with pytest.raises(notifications.TelegramDeliveryError) as failure:
        notifications.deliver_telegram_alert.run(title="Failure", message="Message")
    assert "bot-secret-token" not in str(failure.value)
    assert "sensitive remote body" not in str(failure.value)


def test_deploy_failure_signal_uses_project_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Celery deployment failures create the expected project alert."""
    captured: list[tuple[str, bool]] = []

    async def fake_alert(project_id: str, *, successful: bool) -> None:
        """Capture normalized deployment alert context."""
        captured.append((project_id, successful))

    monkeypatch.setattr(notifications, "_create_deployment_alert", fake_alert)
    notifications.notify_task_failure(
        sender=SimpleNamespace(name="projects.git_deploy"),
        task_id="job-id",
        exception=RuntimeError("secret details"),
        args=(),
        kwargs={"project_id": "project-id"},
    )
    assert captured == [("project-id", False)]


def test_telegram_queue_payload_does_not_contain_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Queued alerts contain content only; workers load credentials from settings."""
    captured: dict[str, Any] = {}

    def fake_send_task(name: str, **kwargs: Any) -> None:
        """Capture the queued task envelope."""
        captured["name"] = name
        captured.update(kwargs)

    monkeypatch.setattr(notifications.settings, "telegram_bot_token", "bot-secret-token")
    monkeypatch.setattr(notifications.settings, "telegram_owner_id", 123456)
    monkeypatch.setattr(notifications.celery_app, "send_task", fake_send_task)
    notifications.enqueue_telegram_alert("Title", "Message")
    assert captured["name"] == "notifications.telegram"
    assert captured["kwargs"] == {"title": "Title", "message": "Message"}
    assert "bot-secret-token" not in repr(captured)


@pytest.mark.asyncio
async def test_owner_alert_persists_and_deduplicates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Repeated active resource alerts persist and deliver only once."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'alerts.db'}")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    deliveries: list[tuple[str, str]] = []
    monkeypatch.setattr(notifications, "SessionFactory", session_factory)
    monkeypatch.setattr(
        notifications,
        "enqueue_telegram_alert",
        lambda title, message: deliveries.append((title, message)),
    )
    first = await notifications.create_owner_alert(
        notification_type="project.down",
        severity="error",
        title="Down",
        message="Project is unavailable",
        resource_type="project",
        resource_id="project-id",
    )
    second = await notifications.create_owner_alert(
        notification_type="project.down",
        severity="error",
        title="Down",
        message="Project is unavailable",
        resource_type="project",
        resource_id="project-id",
    )
    async with session_factory() as session:
        count = await session.scalar(select(func.count()).select_from(Notification))
    await engine.dispose()
    assert first is True
    assert second is False
    assert count == 1
    assert deliveries == [("Down", "Project is unavailable")]
