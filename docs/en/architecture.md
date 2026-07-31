# Architecture

**English** · [Українська](../uk/architecture.md) · [Русский](../ru/architecture.md) · [Polski](../pl/architecture.md)

NectarinePanel is a modular monorepo sized for one VPS and a small operator
team.

```text
Browser -> Nginx -> Nuxt
                 -> FastAPI -> PostgreSQL
                            -> Valkey -> Celery worker
                            -> local system agent -> host services
Telegram -> aiogram -> internal FastAPI endpoints
```

FastAPI owns validation, authorization, state and audit records. Celery owns
long-running non-interactive work. The system agent owns the narrow privileged
boundary. Nuxt contains presentation and client state only.

Global roles and project memberships are enforced at the API boundary. Resource
policies apply CPU/RAM limits to supported Docker and systemd runtimes; disk
limits are monitored. Protected external project roots are downloaded and
archived through the agent, never by granting the API broad filesystem access.

Project releases live below
`/srv/vps-panel/projects/{project_id}/releases/{release_id}`. The `current`
symlink is replaced atomically after a successful release. Shared environment
and persistent application data never live inside a release directory. Docker
projects can bind validated directories below `shared/` into containers.

Runtime activation stays behind the system agent boundary. Docker and Compose
use fixed Docker argv. systemd and PM2 write generated units with
`NoNewPrivileges`, deterministic per-project Linux users, project working
directories and explicit read/write paths. Static sites are served by generated
Nginx static-root configs.

The agent listens only on localhost and exposes no general command runner.
Its HTTP process runs as `vps-panel-agent`; it serializes one validated
operation to a root-owned helper over stdin. Sudo permits that exact helper
path with no arguments. The helper validates the operation protocol again
before mapping it to fixed argv and contained filesystem operations.

Monitoring has two layers:

- FastAPI exposes current host snapshots and WebSocket streaming for the UI.
- Celery beat persists host/project samples and creates deduplicated alerts for
  high host resource use, failed project health checks, and project resource
  policy violations.
