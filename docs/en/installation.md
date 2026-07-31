# Installation

**English** · [Українська](../uk/installation.md) · [Русский](../ru/installation.md) · [Polski](../pl/installation.md)

Production target: Ubuntu 24.04 LTS with a DNS record already pointing at the
server.

Run the installer as root from a trusted, pinned release:

```bash
sudo bash installer/install.sh
```

The installer creates separate `vps-panel` and `vps-panel-agent` system users,
directories below `/srv/vps-panel`, service environment files, systemd units
and an Nginx virtual host. The agent user receives one exact sudoers entry for
the root-owned allowlisted helper. The installer validates both sudoers and
Nginx before starting services, then requests a certificate only after the HTTP
route responds. Docker Compose, the pinned Adminer image and a pinned
foreground PM2 runtime are installed for supported project runtimes.
MariaDB client tools are used for MySQL-compatible database operations to avoid
package conflicts on hosts that already use MariaDB repositories.
APT package installation keeps existing local conffile decisions with
`--force-confold`/`--force-confdef` so production installs do not block on
interactive package prompts.
After Docker package changes the installer retries Docker startup before using
the Docker API, which handles transient socket-activation failures during
package replacement.

Internal ports default to backend `8000`, frontend `3000`, agent `8090` and
Adminer `8081`. On a host that already uses any of those loopback ports, pass
`--backend-port`, `--frontend-port`, `--agent-port` or `--adminer-port`.

`installer/update.sh` builds and checks the new frontend, creates a full panel
and PostgreSQL recovery backup, applies migrations, replaces units and verifies
backend/frontend health. `installer/uninstall.sh` stops all panel-managed
systemd, Docker, Compose and SFTP runtimes before removing the application.
Persistent storage is retained unless `--purge` is explicitly supplied.

Do not pipe an unreviewed moving branch directly into root shell. For a
one-command install URL, publish a versioned script and checksum with every
release.
