# Security

**English** · [Українська](../uk/security.md) · [Русский](../ru/security.md) · [Polski](../pl/security.md)

## Trust boundaries

- Public traffic terminates at Nginx.
- Backend and frontend are unprivileged.
- PostgreSQL, Valkey, Adminer and the system agent stay on private networking.
- Only the dedicated non-root agent user receives one exact helper sudo rule.
- Deployed projects use distinct containers or runtime users.
- Downloads and archives from protected external project roots are staged by the
  agent in a restricted temporary directory.

## Authentication

Passwords use Argon2id. Access tokens expire quickly. Refresh tokens are opaque,
stored only as SHA-256 digests and rotated on every use. Failed and successful
logins are audited. The production Nginx template and Redis-backed backend both
rate-limit login attempts. Global roles and project memberships are enforced
by backend permissions; hiding a frontend action is not an authorization
boundary.

## Secrets

Production startup requires `FIELD_ENCRYPTION_KEY` to contain a valid Fernet
key. Secret values are masked by default and never included in audit details.
Installer-generated environment files are `0600` or `0640` and readable only
by root and the relevant service account.

Private Git credentials, database passwords, SFTP generated passwords and RCON
passwords are encrypted at rest. Deployment workers materialize Git credentials
only into temporary files under the project `shared/` directory and remove them
after clone.

The Telegram bot reads its encrypted runtime token only through the
localhost-only internal API authenticated by `TELEGRAM_API_TOKEN`. Public owner
endpoints return configuration state but never the token.

## Files and archives

All paths are resolved against configured roots and checked after resolution.
ZIP and TAR.GZ extraction reject traversal, links, device files, excessive
entry counts and excessive expanded sizes. Archive output cannot be placed
inside its source directory. Upload size limits apply at Nginx and FastAPI.
Backup downloads require authenticated, expiring, single-use tokens.

Destructive UI actions use confirmation dialogs with exact path/name matching
where the backend also requires confirmation. Project and domain deletion
cannot be triggered by a confirmation that exists only in the browser.

## Webhooks

GitHub webhooks require both the private URL token and a valid
`X-Hub-Signature-256` HMAC signature. The HMAC secret is shown once at webhook
creation and stored encrypted.

## Operational rules

- Never mount the Docker socket into the public backend.
- Never add a generic shell operation to the agent.
- Project cron commands must stay inside Docker/Compose containers, Minecraft
  RCON, or the deterministic per-project Linux user with `current` as cwd.
- Keep the helper, application tree and sudoers entry root-owned.
- Validate Nginx configuration before reload.
- Require explicit confirmation for destructive UI and Telegram actions.
- Review audit logs after credential, domain, database and restore operations.
