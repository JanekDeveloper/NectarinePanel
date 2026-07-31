# Monitoring

**English** · [Українська](../uk/monitoring.md) · [Русский](../ru/monitoring.md) · [Polski](../pl/monitoring.md)

The dashboard reads current host metrics from FastAPI and subscribes to
`/api/v1/monitoring/live` over WebSocket for realtime updates.

Celery beat runs `monitoring.collect` and persists:

- host CPU/RAM/disk/network samples;
- per-project disk usage, healthcheck status and runtime resource metrics;
- deduplicated unread notifications for high disk, high RAM and failed project
  health checks, plus project resource-policy violations.

Project resource metrics come from the system agent. Docker, Compose and
Minecraft runtimes use fixed Docker identifiers and `docker stats`. systemd and
PM2 runtimes use the generated unit MainPID and process-tree memory/CPU
sampling. Static projects report disk and health state only.

Project health checks use the configured project `healthcheck_url`. A 2xx/3xx
response marks the project as healthy and auto-resolves the unread
`project.down` alert. Any network error or non-2xx/3xx response creates an
unread alert.

Disk and memory alert thresholds are owner-configurable. Persisted values
override installer environment defaults and are resolved when each sample is
stored.

Useful API endpoints:

```text
GET /api/v1/monitoring
GET /api/v1/monitoring/history
GET /api/v1/monitoring/projects/latest
GET /api/v1/monitoring/projects/{project_id}/latest
GET /api/v1/monitoring/projects/{project_id}/history
GET /api/v1/monitoring/notifications
POST /api/v1/monitoring/notifications/{notification_id}/read
WS  /api/v1/monitoring/live?token={access_token}
```
