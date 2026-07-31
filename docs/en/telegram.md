# Telegram

**English** · [Українська](../uk/telegram.md) · [Русский](../ru/telegram.md) · [Polski](../pl/telegram.md)

Set `TELEGRAM_BOT_TOKEN` and `TELEGRAM_OWNER_ID` to enable the optional Compose
profile:

```bash
docker compose --profile telegram up -d telegram-bot
```

The bot ignores non-owner commands. Start, stop, restart, restore and backup
actions require inline confirmation. Dumps and archives are sent only to the
configured owner and only below the configured size limit; larger artifacts
use authenticated one-time links.

The worker also delivers proactive owner alerts for:

- successful and failed deployments;
- failed project health checks;
- failed backups;
- failed SSL issue or renewal;
- high host disk or memory usage.

Alerts are persisted in the panel before delivery. Telegram delivery runs as a
retryable Celery task with exponential backoff. The queue payload contains only
the alert text; credentials are never included in task results or error
messages.

At startup the bot loads its effective token, owner allowlist and transfer
limit from the backend through the authenticated internal API. Values saved in
the panel database override installer environment defaults. Restart the bot
service after changing its token or owner ID. Worker delivery resolves the same
persisted settings for every retry.
