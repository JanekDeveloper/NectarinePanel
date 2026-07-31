# Projects

**English** · [Українська](../uk/projects.md) · [Русский](../ru/projects.md) · [Polski](../pl/projects.md)

Projects define a source, runtime and immutable deployment history. Docker is
the production default; static sites use a built output directory served by
Nginx.

Git deployments clone the configured branch into a timestamped release and
atomically update `current`. Uploaded ZIP deployments must pass archive path and
symlink validation before extraction.

Supported runtime activation paths:

- Docker container: build Dockerfile, replace the named project container and pass `shared/runtime.env`.
- Docker Compose: run `docker compose up --detach --build --remove-orphans` with a bounded project name.
- Static: validate the configured output directory and serve it through Nginx static root.
- systemd: generate one restricted `vps-project-{id}.service` unit for the release start command under a deterministic per-project Linux user.
- PM2: run PM2 inside the same restricted systemd unit and per-project user boundary.
- Minecraft Forge: run the configured Forge server container from `/srv/vps-panel/minecraft/{project_id}`.

Built-in templates create starter files plus runtime defaults for FastAPI,
Nuxt, Vue/Vite, React/Vite, Next.js, Node API, Python Telegram bot, Python
Discord bot, static site, Docker Compose app and Minecraft Forge server.

Environment keys follow shell variable naming rules. Secret values are
encrypted at rest when `FIELD_ENCRYPTION_KEY` is configured and always masked in
list responses. The Secrets Manager keeps version history, supports validated
`.env` import and rollback, and requires exact key confirmation for a one-time
secret reveal; audit records never include plaintext values.

Owners and admins manage all projects. Maintainers and viewers only access
projects to which they are assigned, with viewer access read-only. Resource
guardrails can enforce CPU/RAM for Docker and systemd projects and monitor disk
usage; Docker Compose is monitor-only in this version.

Private Git repositories are supported through an encrypted project source
credential:

- token credentials use temporary `GIT_ASKPASS` files;
- deploy keys use temporary private key files with `GIT_SSH_COMMAND`;
- credentials are loaded by the worker from the database and are not placed in
  Celery job payloads.

GitHub auto-deploy webhooks use two secrets: a URL token and a separate
`X-Hub-Signature-256` HMAC secret. Store the HMAC secret as the GitHub webhook
secret and keep the URL token private.

Project logs use an authenticated WebSocket with polling fallback. The console
uses a separate authenticated WebSocket for correlated command/result frames;
the REST endpoint remains available as a fallback. Docker commands execute only
inside the named project container, Minecraft commands use local-only RCON, and
systemd/PM2 or Compose consoles expose only their fixed status/log operations.
Every command is audited by command name and SHA-256 digest without storing
arguments that may contain secrets.

Project cron jobs run through the same agent boundary. Docker jobs execute
inside the fixed project container; Docker Compose jobs execute in
`runtime_config.cron_service` or `app`; systemd/PM2 jobs run as the
deterministic per-project Linux user in `current`; Minecraft jobs use RCON when
enabled. Static projects do not run cron commands.

Project deletion requires the exact project name. The agent first removes the
fixed systemd/PM2 unit and runtime user, labeled Docker or Compose resources,
SFTP account and mount, Nginx hosts, Certbot certificates, and project
directories. Metadata is deleted only after cleanup succeeds. A partial failure
is audited as `project.delete_failed`; repeating the same deletion safely
resumes the idempotent cleanup.

Domain setup and renewal inspect the resulting certificate through fixed
OpenSSL argv. The worker persists expiry and SHA-256 fingerprint directly, so
SSL state does not depend on a browser polling the job endpoint. Scheduled
renewal refreshes metadata for every active domain. Forced manual renewal
requires exact hostname confirmation because Let's Encrypt applies issuance
rate limits.
