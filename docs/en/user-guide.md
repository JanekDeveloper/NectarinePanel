# NectarinePanel User Guide

**English** · [Українська](../uk/user-guide.md) · [Русский](../ru/user-guide.md) ·
[Polski](../pl/user-guide.md)

This guide is for people who deploy and operate projects through the web
interface. Server installation and source-code changes are covered in the
[Developer Guide](developer-guide.md).

> NectarinePanel manages privileged workloads on one VPS. Keep an off-host
> backup, test restores, and give every user the least access they need.

## Installation

Install on a dedicated Ubuntu 24.04 LTS VPS with a domain A/AAAA record already
pointing to it, at least 2 GB RAM, 20 GB free disk, and root or sudo access.
Clone a reviewed release tag, then run the installer as root:

```bash
git clone --branch vX.Y.Z --depth 1 https://github.com/JanekDeveloper/NectarinePanel.git
cd NectarinePanel
sudo ./installer/install.sh --domain panel.example.com --email admin@example.com
```

| Option | Purpose |
| --- | --- |
| `--domain`, `--email` | Public panel domain and Let's Encrypt email. |
| `--admin-user`, `--admin-password` | Initial owner username and password. Prefer an interactive password prompt or a secret store. |
| `--storage-root` | Persistent panel/project storage path. |
| `--telegram-token`, `--telegram-owner-id` | Optional Telegram bot token and owner chat ID. |
| `--public-ip` | VPS public IP when automatic detection is unsuitable. |
| `--source` | Trusted local source checkout instead of cloning. |
| `--repository`, `--ref` | Repository URL and pinned Git tag/ref to install. |
| `--max-upload-mb` | Maximum upload size in MB. |
| `--backend-port`, `--frontend-port`, `--agent-port`, `--adminer-port` | Override occupied loopback ports. |
| `--non-interactive` | Fail instead of prompting for required values. |
| `--skip-ssl` | Skip certificate issuance; use only when TLS is terminated elsewhere. |
| `--dry-run` | Validate the installation plan without changing the host. |

The installer configures services, Nginx, TLS, PostgreSQL, Valkey and the
System Agent. It prints the initial owner password once if `--admin-password`
was not supplied; save it in a password manager. After completion, open
`https://panel.example.com`, sign in, change temporary credentials, and verify
`systemctl status vps-panel-backend vps-panel-worker vps-panel-frontend`.

Use only a pinned release tag; do not run an unreviewed moving branch as root.
For upgrades, see [Updating the panel](#updating-the-panel). Detailed installer
options are in [Installation](installation.md).

## 1. Sign in and choose a language

Open the panel URL and sign in with the account created by the installer or by
an owner. Use the language selector on the sign-in page or in the sidebar.
NectarinePanel supports English, Ukrainian, Russian, and Polish. The choice is
stored in the browser and in a cookie.

If Telegram login is enabled, start login in the panel, open the bot from the
shown link, and approve the short-lived request. Return to the same browser
tab; it completes the session automatically. Never approve a request that you
did not start.

## 2. Understand access roles

| Role | Access |
| --- | --- |
| `owner` | Full panel access, users, security settings, and every project. |
| `admin` | All projects and operational actions, but not owner/security management. |
| `maintainer` | Assigned projects only; deploy, runtime, files, and variable editing. No secret reveal or project deletion. |
| `viewer` | Read-only access to assigned projects, logs, files, and masked variables. |

Project membership matters only for maintainers and viewers. A hidden button
does not grant or revoke access—the backend checks every operation.

## 3. Create a project

Open **Projects → New project**. A template is the easiest starting point; a
custom project exposes all runtime fields.

### Choose a source

- **Git repository**: enter the HTTPS repository URL and branch. Deploys clone
  a new immutable release. You may deploy a specific branch, tag, or commit.
- **Files / ZIP**: create the project without a repository, then upload a
  `.zip` in **Deploys**. Archives with unsafe paths, links, or excessive
  expansion are rejected.

For a private GitHub repository, use one of these methods in project settings:

1. **Fine-grained token**: on GitHub create a fine-grained personal access
   token scoped to the selected repository with **Contents: Read-only**. Save
   it as a token credential in NectarinePanel. Use the normal HTTPS repository
   URL; do not place the token in the URL.
2. **Deploy key**: generate a dedicated SSH key pair, add the public key in
   GitHub under **Repository → Settings → Deploy keys** without write access,
   and save the private key as a deploy-key credential in NectarinePanel. Use
   the repository SSH URL.

Credentials are encrypted and are not copied into deployment job payloads.
Rotate or remove them immediately if repository access changes.

### Choose a runtime

| Runtime | Use it for | Important behavior |
| --- | --- | --- |
| Docker container | Most APIs, bots, and web apps | Builds the repository `Dockerfile`; reliable isolation and resource enforcement. |
| Docker Compose | Multi-container applications | Runs the repository Compose file; resource policy is monitoring-only. |
| Static Nginx | Built static sites | Serves the configured output directory; no application process. |
| systemd | Existing host-native applications | Runs the start command as a dedicated unprivileged project user. |
| PM2 | Node.js applications that need PM2 | PM2 runs inside the restricted project systemd unit. |
| Minecraft Forge | Forge server | Uses its own configuration, console, files, and backup flow. |

The start command does not rewrite a repository `Dockerfile`. For a Docker
runtime, the Dockerfile defines the container command. Change its `CMD` or
`ENTRYPOINT`, or make it call a configurable launcher. Panel start commands are
used by host-native runtimes and templates when the files are first generated.

### Verify before the first deploy

- the branch exists and the credential can read it;
- the build/start commands match the chosen runtime;
- the application listens on `0.0.0.0`, not only `127.0.0.1`, inside Docker;
- the upstream port matches the application port;
- secrets are in **Variables**, not committed to Git;
- persistent paths are mounted below `shared/`;
- an HTTP health-check URL returns a 2xx or 3xx response.

## 4. Deploy and roll back

Open a project and select **Deploys**.

- **Git deploy** optionally accepts a revision. Leaving it empty uses the
  configured branch.
- **ZIP deploy** accepts only `.zip` and is intended for file-based projects.
- The active job shows queued/running/completed/failed state and progress.
- Release history shows revision, timing, path, result, and stored log.

A successful deploy creates a release below
`projects/{project_id}/releases/{release_id}` and atomically points `current`
to it. A failed release does not replace the active one.

To roll back, choose a successful release, confirm its name or revision, and
start rollback. Rollback activates that release; it does not restore databases
or persistent files in `shared/`. Restore those from a matching backup when
needed.

If a job fails, open its log and fix the first relevant error. Repeating a
deploy without changing the source or configuration usually produces the same
result.

## 5. Operate a running project

The overview shows current state and metrics when a sample is available.
Runtime buttons follow the actual state: **Start** appears for a stopped
project; **Stop** and **Restart** appear for a running project.

- **Logs** reads runtime logs and updates live when possible.
- **Console** sends commands to the project runtime. It is not a root shell.
  Docker commands run inside the project container; Minecraft uses RCON;
  systemd, PM2, and Compose expose restricted operations.
- **Cron** runs commands through the same runtime boundary. Static projects do
  not support cron commands.

When the console prints `no job control in this shell`, an interactive shell
was started without a real TTY. Normal commands still work, but interactive job
control such as `Ctrl+Z`, `fg`, and `bg` does not. Prefer non-interactive
commands in the web console.

## 6. Manage variables and secrets

Use **Variables** as the source of runtime environment values.

- Mark credentials, tokens, and passwords as secrets.
- Secret values are encrypted at rest and masked in lists.
- **Reveal** requires typing the exact key and is written to the audit log
  without the plaintext value.
- **History** shows versions. Rollback creates a new version and preserves the
  previous history.
- `.env` import parses literal `KEY=value` entries; it does not execute shell
  expansion. **Merge** updates supplied keys. **Replace** removes keys absent
  from the imported content.

Variable changes do not redeploy automatically. Restart or redeploy the
project so the runtime receives the new environment. Treat a revealed value as
exposed: do not paste it into logs, screenshots, issue trackers, or chat.

## 7. Use the file manager safely

The project root contains:

```text
releases/   immutable deployment releases
current     symlink to the active release
shared/     persistent files kept across deploys
```

Click a folder name or a breadcrumb segment to navigate. Multiple entries can
be selected for supported bulk actions. File and folder sizes are calculated
by the server and may take longer for large trees.

`current` is a symbolic link, not a regular directory. The panel resolves safe
links inside the project root. If an application creates data inside its
container without a `shared/` bind mount, that data may not appear in the file
manager and can disappear when the container is replaced. Put SQLite files,
uploads, and other state in a mounted persistent directory, for example
`shared/data`.

Before deleting or moving files, stop applications that may be writing them.
Use backups for bulk or irreversible changes.

## 8. Domains and TLS

Before adding a domain, create its DNS A/AAAA record for the VPS and wait for
public DNS resolution. In **Domains**:

1. add the hostname and verify the configured upstream port;
2. test the generated HTTP route;
3. issue a Let's Encrypt certificate;
4. confirm HTTPS and the application health check.

Do not repeatedly force certificate issuance while debugging DNS or routing;
Let's Encrypt applies rate limits. Certificate renewal is scheduled and its
expiry/fingerprint is stored by the panel.

## 9. Databases

Database servers and project databases are separate concepts. First configure
server credentials, then create or attach a database. Supported engines are
PostgreSQL, MySQL/MariaDB, SQLite, and Redis/Valkey with engine-specific
capabilities.

- Provisioned connection values can be attached to a project variable.
- Dumps, restores, and deletion run as background jobs.
- Restore must use a compatible dump and requires confirmation.
- Stop the attached project before restoring or deleting SQLite.
- Redis/Valkey provides connection and health information, not fake relational
  tables or isolation.

Always create a fresh dump before a destructive migration and verify the
application after restore.

## 10. Backups and recovery

Automatic backups are off until a policy is configured. A project backup can
include environment, active release, shared data, uploads, logs, database
dumps, and Minecraft data. Full-panel backups can include the panel database,
configuration, generated Nginx hosts, projects, and scheduler state.

Every backup has a manifest and SHA-256 checksum. A green “completed” state
proves archive creation, not recoverability. Production practice is:

1. schedule backups with suitable retention;
2. copy critical artifacts off the VPS;
3. test a restore on a disposable project or host;
4. record the last verified restore date.

Project restore checks the archive type, compatibility, checksum, and target
project. It may replace files, so stop the project and take a new backup first.

## 11. Monitoring and resource guardrails

The main monitoring page reports VPS CPU, RAM, disk, network, and alerts.
Project pages show runtime and disk metrics only when a sample exists. “No
data” means the collector has not produced a valid sample; it is not a zero.

Owners and admins can enable project guardrails:

- CPU and RAM are enforced for supported Docker and host-native runtimes;
- Docker Compose limits are monitored but not enforced;
- disk limits create alerts but are not filesystem quotas;
- Minecraft memory must leave at least 512 MB above the configured JVM `Xmx`.

A health check should be cheap, unauthenticated, and verify that the service is
ready. A failed request or non-2xx/3xx response creates a deduplicated alert.

## 12. Users, audit, and Telegram

Owners manage accounts in **Settings → Users**. Admins have global operational
access; maintainers and viewers must be added to individual projects in the
project settings. The last active owner cannot be disabled.

Review **Audit logs** after permission changes, secret reveals, restores,
domain changes, and destructive operations. Audit details intentionally omit
passwords and secret values.

Telegram is optional. An owner configures the bot token and permitted Telegram
owner ID in settings, then restarts the bot service when the effective token or
owner changes. The bot can deliver alerts and run confirmed operational
actions. Do not treat chat history as secret storage.

## Updating the panel

Update from a pinned, reviewed release during a maintenance window. First make
and verify an off-host backup of the panel and the projects that matter.

1. Connect to the VPS and select a release tag, not an unreviewed moving branch.
2. Run `sudo /opt/nectarine-panel/installer/update.sh --ref vX.Y.Z`.
3. Wait for the updater to back up the panel, apply migrations, build the web
   client, restart services, and complete its health check.
4. Sign in again and verify projects, monitoring, deployments, and persistent
   data. Inspect `systemctl status vps-panel-backend vps-panel-worker` if the
   health check fails.

Use `--dry-run` to inspect an update first. Use `--source` only for a trusted
local checkout. Do not interrupt the updater or replace files manually while it
is running; restore from the verified backup if recovery is required.

## 13. Routine production checklist

Daily or after an alert:

- check failed projects, health checks, disk, and unread notifications;
- inspect the relevant deployment/runtime log before restarting repeatedly;
- confirm that scheduled backups continue to complete.

After every release or configuration change:

- verify application health and logs;
- verify the public HTTPS endpoint;
- confirm persistent data survived the deployment;
- roll back promptly if the release is unhealthy.

Regularly:

- update NectarinePanel from a tagged release;
- rotate user, Git, database, Telegram, and application credentials;
- remove unused accounts, project memberships, domains, and backups;
- perform and document an off-host restore test.

## 14. Common problems

| Symptom | Check |
| --- | --- |
| Git deploy cannot clone | Repository URL, branch, token/deploy-key read access, and credential expiry. |
| Deploy succeeds but service is down | Runtime log, listen address, upstream port, environment, and health-check path. |
| File is missing after redeploy | It was written inside an immutable release/container instead of `shared/`. |
| `current` does not open | No successful release exists, or the link target was removed; inspect release history. |
| Metrics show no data | Project has not run yet, collector/agent is unavailable, or the runtime does not expose that metric. |
| TLS issue fails | Public DNS, ports 80/443, Nginx route, and issuance rate limits. |
| Action returns 403 | Your role or project membership does not include that operation. |
| Action returns 502 | The backend could not complete a validated agent operation; an owner should inspect panel and agent logs. |

For server-side diagnostics, installation, updates, and recovery, continue with
the [Developer Guide](developer-guide.md). Technical reference material remains
available under [`docs/`](./).
