<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="./nectarinepanel_dark_logo_wordmark.png">
    <img src="./nectarinepanel_logo_wordmark.png" alt="NectarinePanel" width="520">
  </picture>
</p>

<p align="center">
  Self-hosted deployment and operations control plane for a single VPS.
</p>

<p align="center">
  <strong>English</strong> · <a href="README.uk.md">Українська</a> ·
  <a href="README.ru.md">Русский</a> · <a href="README.pl.md">Polski</a>
</p>

> [!IMPORTANT]
> NectarinePanel is not yet at version 1.0 and has privileged access to your
> server. Before upgrading, verify that recovery works, keep backups outside
> the VPS, and install tagged releases instead of code from a moving branch.

## What it does

NectarinePanel deploys and operates websites, APIs, bots, Docker workloads, and
Minecraft Forge servers on one Ubuntu VPS. FastAPI owns authentication and
state, Celery runs long jobs, and a localhost-only system agent performs a
strict allowlist of privileged host operations.

Core capabilities:

- Git and ZIP deploys, private repository credentials, GitHub webhooks,
  release history, logs, and rollback;
- Docker, Docker Compose, static Nginx, systemd, PM2, and Minecraft Forge
  runtimes;
- start, stop, restart, console, cron jobs, file manager, SFTP, domains, and
  Let's Encrypt certificates;
- PostgreSQL, MySQL/MariaDB, SQLite, Redis/Valkey connections, dumps, restores,
  and protected Adminer access;
- project and full-panel backups with checksums, manifests, retention, and
  optional encryption;
- host/project monitoring, health checks, alerts, and Telegram notifications;
- encrypted Secrets Manager with masked values, reveal audit, version history,
  `.env` import, and rollback;
- owner/admin/maintainer/viewer RBAC and project memberships;
- per-project CPU/RAM guardrails and disk-limit monitoring.

## Screenshots

The interface uses a focused dark control-center layout. These screens show the
main workflow from sign-in to project creation and operations:

<p align="center">
  <img src="docs/images/login.png" alt="NectarinePanel sign-in" width="48%">
  <img src="docs/images/overview.png" alt="NectarinePanel VPS overview" width="48%">
</p>
<p align="center">
  <img src="docs/images/project-create.png" alt="Project creation foundation step" width="48%">
  <img src="docs/images/project-and-source-code.png" alt="Project and source configuration" width="48%">
</p>
<p align="center">
  <img src="docs/images/runtime-and-build.png" alt="Runtime and build configuration" width="48%">
  <img src="docs/images/review-configuration.png" alt="Project configuration review" width="48%">
</p>
<p align="center">
  <img src="docs/images/empty-example.png" alt="Project overview with empty states" width="70%">
</p>

## Architecture

```text
Browser -> Nginx -> Nuxt
                 -> FastAPI -> PostgreSQL
                            -> Valkey -> Celery worker / scheduler
                            -> localhost system agent -> host services / Docker
Telegram -> aiogram -> internal authenticated FastAPI endpoints
```

The backend never exposes a generic root shell. Privileged changes cross an
authenticated agent boundary, are validated against typed operations, and map
to fixed command arguments and contained filesystem paths.

See [architecture](docs/en/architecture.md) and [security](docs/en/security.md).

## Requirements

Production:

- Ubuntu 24.04 LTS on a dedicated VPS;
- a domain with an A/AAAA record pointing to the VPS;
- root or sudo access;
- at least 2 GB RAM and 20 GB free disk space; workload capacity is additional.

Development:

- Python 3.12+;
- Node.js 22.22.2 LTS, 24.15+ LTS, or 26+;
- npm 10+;
- Docker Engine with Compose v2.

## Production installation

Review the installer, clone a tagged release, and run it as root:

```bash
git clone --branch v0.1.0 --depth 1 \
  https://github.com/JanekDeveloper/NectarinePanel.git
cd NectarinePanel
sudo ./installer/install.sh \
  --domain panel.example.com \
  --email admin@example.com
```

Without `--admin-password`, the installer generates a password and prints it
once. It creates PostgreSQL/Redis, dedicated system users, systemd services,
Nginx, TLS, the system-agent boundary, and the first owner account.

After a tagged release is published, the equivalent one-command installer is:

```bash
curl -fsSL \
  https://raw.githubusercontent.com/NectarinePanel/NectarinePanel/v0.1.0/installer/install.sh \
  | sudo bash -s -- --domain panel.example.com --email admin@example.com
```

Do not replace the version tag with `main` in a root-shell command. Full
installation options are documented in [docs/en/installation.md](docs/en/installation.md).

## First project

1. Sign in with the owner credentials printed by the installer.
2. Open **Projects → New project** and choose a template or custom runtime.
3. Configure a Git URL or upload a ZIP. Private GitHub repositories support a
   fine-grained token or a read-only deploy key.
4. Store credentials in **Variables** and persistent container data in
   `shared/` mounts.
5. Deploy from the **Deploys** tab, then attach a domain and issue TLS.
6. Configure a backup policy and verify one restore before relying on it.

Project operations are covered in [docs/en/projects.md](docs/en/projects.md).

## Local development

```bash
cp .env.example .env
# Replace development secrets in .env before sharing the environment.
docker compose up --build
docker compose exec backend python -m app.cli create-admin --username admin
```

Open `http://localhost:3000`; OpenAPI is available at
`http://localhost:8000/api/v1/docs`. Development ports bind to loopback only.
The development configuration is not a production deployment.

For host development:

```bash
python3 -m venv .venv
make setup
make migrate
make backend
make frontend
```

## Verification

```bash
make lint
make test
make build-frontend
make audit-frontend
make compose-check
```

`make check` runs the complete local gate. GitHub Actions repeats Python,
frontend, installer, Compose, and container-build checks on every pull request.

## Update and removal

```bash
sudo /opt/nectarine-panel/installer/update.sh
sudo /opt/nectarine-panel/installer/uninstall.sh
```

Uninstall preserves configuration and data unless `--purge` is explicitly
confirmed. The updater creates a recovery backup before migrations and service
replacement.

## Current scope

- one Ubuntu VPS, not a multi-node orchestrator;
- local backup storage only; copy critical backups off the VPS;
- Docker Compose resource policies are monitor-only;
- disk limits alert but do not create filesystem quotas;
- Cloudflare DNS automation is not included;
- the web interface and primary user/developer guides support English,
  Ukrainian, Russian, and Polish.

## Documentation

- [User Guide](docs/en/user-guide.md) — deploying and operating projects through
  the web interface;
- [Developer Guide](docs/en/developer-guide.md) — architecture, local setup,
  development, testing, installation, releases, and diagnostics;
- translations: [Українська](docs/uk/user-guide.md),
  [Русский](docs/ru/user-guide.md), [Polski](docs/pl/user-guide.md).

Technical reference:

- [Installation](docs/en/installation.md)
- [Architecture](docs/en/architecture.md)
- [Security](docs/en/security.md)
- [Projects and deployments](docs/en/projects.md)
- [Databases](docs/en/databases.md)
- [Backups](docs/en/backups.md)
- [Monitoring](docs/en/monitoring.md)
- [Telegram](docs/en/telegram.md)
- [Minecraft Forge](docs/en/minecraft-forge.md)
- [Development](docs/en/development.md)
- [Release process](docs/en/releasing.md)

## Contributing and security

Read [CONTRIBUTING.md](CONTRIBUTING.md) before opening a pull request. Report
vulnerabilities privately according to [SECURITY.md](SECURITY.md), never in a
public issue.

NectarinePanel is available under the [MIT License](LICENSE).
