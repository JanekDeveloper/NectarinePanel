# Backups

**English** · [Українська](../uk/backups.md) · [Русский](../ru/backups.md) · [Polski](../pl/backups.md)

Automatic backups are disabled until the owner configures a schedule. A new
policy defaults to project `.env` plus attached ready databases. In backups the
logical `.env` item maps to the generated `shared/runtime.env` file.

Manual project backups can include the full project root or explicit file
groups: `.env`, `current`, `shared`, `uploads`, `logs`, and Minecraft server
paths such as `world`, `mods`, `config`, `server.properties`, `whitelist.json`,
and `ops.json`.

Global full-panel backups are available from `/backups`. They can include:

- panel database dump or SQLite copy;
- `/etc/nectarine-panel` service configuration;
- `/etc/nginx/vps-panel` generated virtual hosts;
- project files, Minecraft files, local SQLite databases and scheduler state.

The active backups directory is intentionally excluded from full-panel archives
to avoid recursive backup growth.

Every archive has an adjacent JSON manifest containing type, timestamp,
resource IDs, included items, size, SHA-256 checksum, application version and
restore compatibility. Project restore verifies checksum, backup type,
compatibility, and target project ID before replacing data.

Local storage layout:

```text
/srv/vps-panel/backups/projects/
/srv/vps-panel/backups/databases/
/srv/vps-panel/backups/full/
```

Retention applies independently to project backups and each attached database,
and keeps at least one successful artifact. Copy critical backups away from the
VPS; local-only storage does not protect against disk or host loss.
