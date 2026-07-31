#!/usr/bin/env bash
set -Eeuo pipefail

readonly PANEL_USER="vps-panel"
readonly AGENT_USER="vps-panel-agent"
readonly INSTALL_DIR="/opt/nectarine-panel"
readonly CONFIG_DIR="/etc/nectarine-panel"
readonly LOCK_FILE="/run/lock/nectarine-panel-installer.lock"
readonly SERVICES=(
    vps-panel-frontend.service
    vps-panel-backend.service
    vps-panel-worker.service
    vps-panel-scheduler.service
    vps-panel-telegram-bot.service
    vps-panel-agent.service
)

PURGE=false
ASSUME_YES=false
DRY_RUN=false

log() {
    printf '[NectarinePanel] %s\n' "$*"
}

fail() {
    printf '[NectarinePanel] ERROR: %s\n' "$*" >&2
    exit 1
}

while (($#)); do
    case "$1" in
        --purge) PURGE=true; shift ;;
        --yes) ASSUME_YES=true; shift ;;
        --dry-run) DRY_RUN=true; shift ;;
        --help|-h)
            printf 'Usage: uninstall.sh [--purge] [--yes] [--dry-run]\n'
            exit 0
            ;;
        *) fail "Unknown argument: $1" ;;
    esac
done

if [[ "$DRY_RUN" == true ]]; then
    log "Would remove panel services and application (purge=$PURGE)"
    exit 0
fi

[[ "$(id -u)" -eq 0 ]] || fail "Run the uninstaller as root"
exec 9>"$LOCK_FILE"
flock -n 9 || fail "Another installer process is running"

if [[ "$ASSUME_YES" == false ]]; then
    prompt="REMOVE"
    [[ "$PURGE" == true ]] && prompt="PURGE"
    read -r -p "Type $prompt to continue: " confirmation
    [[ "$confirmation" == "$prompt" ]] || fail "Cancelled"
fi

PANEL_DOMAIN=""
STORAGE_ROOT="/srv/vps-panel"
if [[ -r "$CONFIG_DIR/panel.env" ]]; then
    PANEL_DOMAIN="$(sed -n 's/^PANEL_DOMAIN=//p' "$CONFIG_DIR/panel.env")"
    STORAGE_ROOT="$(sed -n 's/^STORAGE_ROOT=//p' "$CONFIG_DIR/panel.env")"
fi

log "Stopping managed project runtimes"
shopt -s nullglob
for unit in /etc/systemd/system/vps-project-*.service; do
    if grep -q '^# Managed by NectarinePanel' "$unit"; then
        systemctl disable --now "$(basename "$unit")" >/dev/null 2>&1 || true
        rm -f "$unit"
    fi
done
for unit in /etc/systemd/system/*.mount; do
    if grep -q '^# Managed by NectarinePanel' "$unit"; then
        systemctl disable --now "$(basename "$unit")" >/dev/null 2>&1 || true
        rm -f "$unit"
    fi
done
shopt -u nullglob

mapfile -t managed_containers < <(
    docker ps -aq --filter label=io.nectarine.project 2>/dev/null || true
)
while read -r container_id compose_project; do
    [[ "$compose_project" =~ ^vps-project-[a-f0-9-]{36}$ ]] || continue
    managed_containers+=("$container_id")
done < <(
    docker ps -a \
        --filter label=com.docker.compose.project \
        --format '{{.ID}} {{.Label "com.docker.compose.project"}}' 2>/dev/null || true
)
if ((${#managed_containers[@]})); then
    mapfile -t managed_containers < <(printf '%s\n' "${managed_containers[@]}" | sort -u)
    docker rm --force "${managed_containers[@]}" >/dev/null 2>&1 || true
fi

while IFS=: read -r username _; do
    [[ "$username" == vps_* ]] || continue
    userdel "$username" >/dev/null 2>&1 || true
done < /etc/passwd
rm -f /etc/ssh/sshd_config.d/90-vps-panel-*.conf
if sshd -t; then
    systemctl reload ssh.service || true
fi

systemctl disable --now "${SERVICES[@]}" >/dev/null 2>&1 || true
docker rm --force nectarine-adminer >/dev/null 2>&1 || true
for service in "${SERVICES[@]}"; do
    rm -f "/etc/systemd/system/$service"
done
rm -f \
    /etc/nginx/sites-enabled/nectarine-panel.conf \
    /etc/nginx/sites-available/nectarine-panel.conf \
    /etc/nginx/conf.d/nectarine-projects.conf
if nginx -t; then
    systemctl reload nginx || true
fi
rm -rf -- "$INSTALL_DIR"
rm -f /usr/local/libexec/nectarine-agent-helper /etc/sudoers.d/nectarine-agent
systemctl daemon-reload

if id "$PANEL_USER" >/dev/null 2>&1; then
    userdel "$PANEL_USER"
fi
if id "$AGENT_USER" >/dev/null 2>&1; then
    userdel "$AGENT_USER"
fi

if [[ "$PURGE" == true ]]; then
    systemctl daemon-reload
    runuser -u postgres -- psql --set=ON_ERROR_STOP=1 <<'SQL'
SELECT pg_terminate_backend(pid)
FROM pg_stat_activity
WHERE datname = 'nectarine' AND pid <> pg_backend_pid();
DROP DATABASE IF EXISTS nectarine;
DROP ROLE IF EXISTS nectarine;
SQL
    if [[ -n "$PANEL_DOMAIN" ]] && command -v certbot >/dev/null; then
        certbot delete --cert-name "$PANEL_DOMAIN" --non-interactive >/dev/null 2>&1 || true
    fi
    rm -rf -- "$CONFIG_DIR" "$STORAGE_ROOT" /etc/nginx/vps-panel /var/lib/vps-panel
    log "Panel and persistent data removed"
else
    log "Application removed. Data and configuration were preserved."
    log "Run with --purge to remove persistent data."
fi
