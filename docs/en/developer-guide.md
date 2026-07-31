# NectarinePanel Developer Guide

**English** · [Українська](../uk/developer-guide.md) ·
[Русский](../ru/developer-guide.md) · [Polski](../pl/developer-guide.md)

This guide is the entry point for contributors and operators who install,
change, test, release, or troubleshoot NectarinePanel. The task-oriented
[User Guide](user-guide.md) covers the web interface. Focused files in `docs/`
remain technical reference material.

## 1. Project scope

NectarinePanel is a modular monorepo for deploying and operating workloads on
one Ubuntu VPS. It is not a multi-node scheduler. The architecture deliberately
keeps validation and authorization in FastAPI, long jobs in Celery, and
privileged host changes behind a narrow local agent.

Supported components:

- FastAPI, SQLAlchemy async, Alembic, PostgreSQL, and Valkey;
- Celery worker and scheduler;
- Nuxt 4, Vue 3, TypeScript, and Pinia;
- a localhost system agent and root-owned allowlisted helper;
- optional aiogram Telegram bot;
- Nginx, systemd, Docker/Compose, PM2, Certbot, and database clients.

Production target: Ubuntu 24.04 LTS. Development requires Python 3.12+, a
supported Node.js LTS version from `frontend/package.json`, npm 10+, and Docker
Engine with Compose v2.

## 2. Repository map

```text
backend/        FastAPI API, models, schemas, services, Alembic migrations
worker/         Celery tasks and job runner
agent/          validated system operations and local HTTP boundary
frontend/       Nuxt application, components, composables, locales, tests
telegram-bot/   optional owner bot and handlers
installer/      production installer, updater, uninstaller, units and Nginx
docs/           guides and focused technical references
tests/          repository, installer and container asset tests
scripts/        maintenance and verification scripts
```

Keep changes in the existing pragmatic structure. Do not introduce additional
domain/application/infrastructure layers unless a concrete feature needs them.
Routes handle HTTP concerns, services hold reusable business/infrastructure
logic, schemas define public contracts, and models define persistence.

## 3. Architecture and request flow

```text
Browser -> Nginx -> Nuxt
                 -> FastAPI -> PostgreSQL
                            -> Valkey -> Celery worker / scheduler
                            -> local agent -> root-owned helper -> host
Telegram -> internal authenticated FastAPI endpoints
```

Typical asynchronous operation:

1. FastAPI authenticates the user, verifies RBAC, validates input, and creates
   a job/audit record.
2. A Celery task receives identifiers and non-secret parameters.
3. The worker reads current state and encrypted credentials from storage.
4. Privileged actions are sent to a typed agent endpoint.
5. The agent and helper validate the operation again and map it to fixed argv
   or contained filesystem operations.
6. The worker records progress and result; the UI follows WebSocket updates
   with polling fallback.

Never add a generic command runner to the agent. Never mount the Docker socket
into the public backend. More detail: [architecture](architecture.md),
[security](security.md), and [projects](projects.md).

## 4. Local setup

All Python commands must use the repository virtual environment.

```bash
python3 -m venv .venv
.venv/bin/pip install -r backend/requirements-dev.txt
cd frontend && npm ci
cd ..
cp .env.example .env
```

Replace the development secrets in `.env` before sharing the environment.
Generate values without installing global Python packages:

```bash
openssl rand -hex 32
.venv/bin/python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

### Complete Docker environment

This is the simplest way to exercise all service interactions:

```bash
docker compose up --build
docker compose exec backend python -m app.cli create-admin --username admin
```

Open the panel at `http://localhost:3000` and OpenAPI at
`http://localhost:8000/api/v1/docs`. Development ports bind to loopback. The
Compose configuration is for local development, not production deployment.

Optional profiles:

```bash
docker compose --profile telegram up --build
docker compose --profile adminer up --build
```

### Run components on the host

Start PostgreSQL/Valkey or configure compatible URLs in `.env`, then:

```bash
make migrate
make backend
make worker
make scheduler
make agent
make frontend
```

These are separate long-running commands. The backend defaults to local SQLite
when run directly, but worker, live jobs, rate limiting, and scheduled tasks
still need Valkey. Runtime and host-operation tests should use mocks or a
disposable environment; do not point development at production storage.

## 5. Configuration

The authoritative settings model is `backend/app/core/config.py`; `.env.example`
documents the normal environment. Important values:

| Variable | Purpose |
| --- | --- |
| `ENVIRONMENT` | `development`, `test`, or a production value. Production rejects placeholder secrets. |
| `DATABASE_URL` | Async SQLAlchemy connection URL. |
| `REDIS_URL` | Valkey/Redis for rate limits, Celery, and live state. |
| `JWT_SECRET` | At least 32 characters outside local environments. |
| `FIELD_ENCRYPTION_KEY` | Fernet key used for stored secrets; required in production. |
| `AGENT_URL`, `AGENT_TOKEN` | Private agent endpoint and shared authentication secret. |
| `STORAGE_ROOT` | Root for projects, backups, uploads, and runtime state. |
| `CONFIG_ROOT`, `NGINX_CONFIG_ROOT` | Panel and generated host configuration. |
| `CORS_ORIGINS` | Comma-separated allowed browser origins. |
| `PUBLIC_BASE_URL` | Public panel URL used for links and callbacks. |
| `TELEGRAM_*` | Optional bot and internal service authentication. |

Do not change `FIELD_ENCRYPTION_KEY` without a planned data migration; existing
encrypted fields would become unreadable. Never log settings objects, tokens,
passwords, private keys, or decrypted database URLs.

## 6. Backend development

The backend is async. Keep database access within `AsyncSession`, use Pydantic
schemas at API boundaries, and return stable HTTP errors instead of exposing
internal exceptions.

When adding an endpoint:

1. define or update a schema under `backend/app/schemas/`;
2. add reusable logic under `backend/app/services/` when the route would
   otherwise mix HTTP handling with business or infrastructure behavior;
3. enforce `require_global_role(...)` or
   `require_project_permission(project_id, permission)` before loading
   sensitive data;
4. audit security-sensitive and mutating actions without plaintext secrets;
5. add unit/API tests for success, validation, unauthenticated access, forbidden
   roles, wrong-project access, and important failure paths;
6. preserve existing response contracts unless a breaking change is explicit.

Project-global roles are owner/admin. Maintainer and viewer access comes from
memberships and the permission map in `backend/app/services/permissions.py`.
Frontend visibility is convenience only; backend authorization is mandatory.

All Python modules, functions, and classes require concise English docstrings.
Use parameter-safe driver calls, validated identifiers, fixed command argv, and
bounded I/O. Never construct a shell command from user input.

## 7. Database migrations

Every persistence change needs an Alembic migration from the current head.
Import all models before autogeneration, then run:

```bash
cd backend
../.venv/bin/alembic heads
../.venv/bin/alembic revision --autogenerate -m "describe change"
../.venv/bin/alembic upgrade head
cd ..
```

Review generated DDL, names, indexes, foreign keys, defaults, upgrade order, and
rollback safety. Test upgrade from the previous schema. Do not edit a migration
that may already be applied; add a corrective migration. Production updater
creates a recovery backup before applying migrations, but the migration must
still be transactional and compatible with existing data where the engine
allows it.

## 8. Worker and jobs

Use Celery for deploys, backups, database work, certificate operations,
monitoring, and other bounded long tasks. API handlers should enqueue and
return a job rather than hold an HTTP connection.

Job rules:

- payloads contain IDs and required non-secret input, not credentials;
- the worker reloads current database state when it starts;
- progress and errors are bounded and safe to show to an operator;
- retries are explicit and limited to idempotent or safely resumable work;
- cleanup is performed in `finally` paths;
- metadata is committed only after the external operation succeeds;
- repeated cleanup/delete operations must be idempotent where practical.

Tests should mock agent/client boundaries and verify both success and partial
failure. Do not rely on a live Docker daemon or public service in unit tests.

## 9. System agent development

The agent is the privileged security boundary. Its HTTP process runs as
`vps-panel-agent` and sends one validated operation over stdin to the exact
root-owned helper permitted by sudoers.

A new operation requires all of the following:

1. a typed request model with strict limits;
2. authorization using the agent token;
3. path resolution below an allowed root after symlink resolution;
4. fixed executable and argument construction without `shell=True`;
5. timeout, output limit, and predictable error mapping;
6. duplicate validation in the helper when root privileges are involved;
7. agent security tests for traversal, injection, links, malformed input, and
   unauthorized access;
8. the narrowest possible filesystem ownership and system permissions.

If an operation cannot be safely expressed as a fixed allowlisted action, it
does not belong in the agent API.

## 10. Frontend development and localization

The Nuxt frontend is presentation and client state. Keep authorization,
secrets, and host logic on the backend. Reuse current components, CSS variables,
Tabler icons, composables, and responsive patterns; add no dependency for a
small utility that is easy to implement and test locally.

Relevant directories:

```text
frontend/pages/        route pages
frontend/components/   reusable UI
frontend/composables/  API, locale, permissions, and shared behavior
frontend/stores/       Pinia session/state
frontend/locales/      English, Ukrainian, Russian, and Polish catalogs
frontend/types/        public frontend types
frontend/tests/        Vitest tests
```

Every user-visible string must use the locale helper and exist in all four
catalogs. Keep translation keys semantic and stable. Format dates and numbers
through locale-aware helpers. For every page implement intentional loading,
error, empty, forbidden, and success states. Destructive operations require a
confirmation dialog and backend confirmation where supported.

Before finishing a UI change:

```bash
cd frontend
npm run lint
npm run typecheck
npm run test
npm run build
cd ..
```

## 11. Tests and quality gate

Run targeted tests while developing:

```bash
.venv/bin/python -m pytest backend/tests/test_projects.py
.venv/bin/python -m pytest agent/tests/test_security.py
cd frontend && npm run test -- projects-utils && cd ..
```

Run the complete gate before commit:

```bash
make check
```

It covers Ruff lint/format, mypy, ESLint, Nuxt typecheck, Python and frontend
tests, frontend production build, npm audit, shell syntax, and Compose config.
For changes affecting images or installation also run:

```bash
docker compose --profile telegram build backend frontend worker agent telegram-bot
sudo ./installer/install.sh --domain panel.example.com --email admin@example.com --dry-run
```

New logic requires tests, including edge cases. Prefer unit tests; add
integration coverage when behavior crosses a database, queue, agent, or
filesystem boundary that mocks cannot prove.

## 12. Storage and deployment invariants

Default production layout:

```text
/opt/nectarine-panel/              installed application
/etc/nectarine-panel/              service configuration and secrets
/etc/nginx/vps-panel/              generated project hosts
/srv/vps-panel/projects/{id}/      releases, current, shared, uploads, logs
/srv/vps-panel/backups/            project, database, and full backups
/srv/vps-panel/minecraft/{id}/     Minecraft runtime data
```

Releases are immutable. Activation replaces `current` atomically. Persistent
application data belongs below `shared/`, never inside a release or an
unmounted container layer. Archive extraction must reject traversal, escaping
links, special files, excessive entry counts, and expansion bombs. Backup
restore must verify checksum, type, compatibility, and target before replacing
data.

## 13. Production installation and lifecycle

Install a reviewed, tagged release as root:

```bash
sudo ./installer/install.sh \
  --domain panel.example.com \
  --email admin@example.com
```

The installer creates dedicated users, PostgreSQL/Redis, storage, Python
environment, frontend build, migrations, systemd units, Nginx, the agent helper,
first owner, and TLS. An omitted admin password is generated and shown once.

Installer options:

| Option | Purpose |
| --- | --- |
| `--domain`, `--email` | Panel hostname and Let's Encrypt email. |
| `--admin-user`, `--admin-password` | Initial owner; password must be at least 12 characters. |
| `--storage-root` | Absolute persistent-data root. |
| `--telegram-token`, `--telegram-owner-id` | Optional bot configuration. |
| `--public-ip` | Explicit public address when auto-detection is unsuitable. |
| `--source` | Install from a trusted local source tree. |
| `--repository`, `--ref` | Repository and pinned Git ref to install. |
| `--max-upload-mb` | Upload limit from 1 to 4096 MiB. |
| `--backend-port`, `--frontend-port`, `--agent-port`, `--adminer-port` | Unique loopback ports from 1024 to 65535. |
| `--non-interactive` | Fail instead of prompting for missing required input. |
| `--skip-ssl` | Configure HTTP without requesting a certificate. |
| `--dry-run` | Validate arguments and intended setup without installation. |

Update and remove:

```bash
sudo /opt/nectarine-panel/installer/update.sh --ref vX.Y.Z
sudo /opt/nectarine-panel/installer/uninstall.sh
```

`update.sh` supports `--source`, `--repository`, `--ref`, and `--dry-run`. It
builds and checks the frontend, creates recovery backups, applies migrations,
replaces units, and verifies health. `uninstall.sh` preserves persistent data
unless `--purge` is explicitly confirmed; `--yes` is intended for automation
and `--dry-run` previews the action.

Never pipe an unreviewed moving branch into a root shell. Release details are
in [installation](installation.md) and [release process](releasing.md).

## 14. Production diagnostics

Start with service state and bounded recent logs:

```bash
sudo systemctl status \
  vps-panel-backend vps-panel-frontend vps-panel-worker \
  vps-panel-scheduler vps-panel-agent vps-panel-telegram-bot
sudo journalctl -u vps-panel-backend -n 200 --no-pager
sudo journalctl -u vps-panel-worker -n 200 --no-pager
sudo journalctl -u vps-panel-agent -n 200 --no-pager
sudo nginx -t
curl -fsS http://127.0.0.1:8000/api/v1/health
```

Use the configured backend port if it differs from 8000. Also check PostgreSQL,
Redis, Docker, disk/inodes, DNS, ports 80/443, and ownership under the storage
root. A `502` from a project operation generally means the backend could not
complete a validated agent action; correlate backend, worker, and agent logs by
time and job ID.

Do not paste complete environment files or unredacted logs into issues. Remove
tokens, cookies, passwords, private URLs, SSH keys, database DSNs, and user data.
Before manual repair, create a backup and understand which component owns the
state. Do not bypass the agent with ad-hoc root changes that the panel cannot
later reconcile.

## 15. Pull requests and releases

Keep commits focused and document migration, security, backup compatibility,
and operational impact. Before a pull request:

1. inspect `git diff` and `git status`;
2. run `make check` and affected container builds;
3. confirm new UI text exists in four locales;
4. confirm no `.env`, database, archive, token, private key, hostname, or
   generated build output was added;
5. update the User Guide for changed workflows and the Developer Guide or
   focused references for changed internals.

For a release, update the changelog and application version, verify migration
order, create a signed `vX.Y.Z` tag, publish checksums and upgrade/rollback
notes, test installation on clean Ubuntu 24.04, and prove a restore on a
disposable host. Follow [CONTRIBUTING.md](../../CONTRIBUTING.md),
[SECURITY.md](../../SECURITY.md), and [releasing.md](releasing.md).
