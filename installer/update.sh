#!/usr/bin/env bash
set -Eeuo pipefail

readonly PANEL_USER="vps-panel"
readonly AGENT_USER="vps-panel-agent"
readonly INSTALL_DIR="/opt/nectarine-panel"
readonly CONFIG_DIR="/etc/nectarine-panel"
readonly LOCK_FILE="/run/lock/nectarine-panel-installer.lock"
readonly ADMINER_IMAGE="adminer:5.4.2-standalone"
readonly PM2_VERSION="7.0.1"
readonly SERVICES=(
    vps-panel-frontend.service
    vps-panel-backend.service
    vps-panel-worker.service
    vps-panel-scheduler.service
    vps-panel-telegram-bot.service
    vps-panel-agent.service
)

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_DIR="${NECTARINE_SOURCE_DIR:-}"
REPOSITORY_URL="${NECTARINE_REPOSITORY_URL:-}"
REPOSITORY_REF="${NECTARINE_REPOSITORY_REF:-}"
DRY_RUN=false
TEMP_DIR=""
BACKUP_DIR=""
HOST_BACKUP_DIR=""
STORAGE_ROOT=""

cleanup() {
    if [[ -n "$TEMP_DIR" && -d "$TEMP_DIR" ]]; then
        rm -rf -- "$TEMP_DIR"
    fi
}
trap cleanup EXIT

log() {
    printf '[NectarinePanel] %s\n' "$*"
}

fail() {
    printf '[NectarinePanel] ERROR: %s\n' "$*" >&2
    exit 1
}

while (($#)); do
    case "$1" in
        --source) SOURCE_DIR="${2:?Missing source path}"; shift 2 ;;
        --repository) REPOSITORY_URL="${2:?Missing repository URL}"; shift 2 ;;
        --ref) REPOSITORY_REF="${2:?Missing git ref}"; shift 2 ;;
        --dry-run) DRY_RUN=true; shift ;;
        --help|-h)
            printf 'Usage: update.sh [--source PATH] [--repository URL] [--ref REF] [--dry-run]\n'
            exit 0
            ;;
        *) fail "Unknown argument: $1" ;;
    esac
done

if [[ "$DRY_RUN" == true ]]; then
    bash -n "$SCRIPT_DIR/install.sh" "$SCRIPT_DIR/update.sh" "$SCRIPT_DIR/uninstall.sh"
    sh -n "$SCRIPT_DIR/helpers/nectarine-agent-helper"
    log "Updater dry-run successful"
    exit 0
fi

[[ "$(id -u)" -eq 0 ]] || fail "Run the updater as root"
[[ -f "$CONFIG_DIR/panel.env" && -d "$INSTALL_DIR" ]] || fail "Panel is not installed"
exec 9>"$LOCK_FILE"
flock -n 9 || fail "Another installer process is running"

if ! id "$AGENT_USER" >/dev/null 2>&1; then
    useradd --system --user-group --home-dir "$INSTALL_DIR/agent" \
        --shell /usr/sbin/nologin "$AGENT_USER"
fi

if [[ -r "$CONFIG_DIR/source.env" ]]; then
    stored_repository="$(sed -n 's/^NECTARINE_REPOSITORY_URL=//p' "$CONFIG_DIR/source.env")"
    stored_ref="$(sed -n 's/^NECTARINE_REPOSITORY_REF=//p' "$CONFIG_DIR/source.env")"
    REPOSITORY_URL="${REPOSITORY_URL:-$stored_repository}"
    REPOSITORY_REF="${REPOSITORY_REF:-$stored_ref}"
fi
REPOSITORY_REF="${REPOSITORY_REF:-main}"
[[ "$REPOSITORY_URL" != *[[:space:]]* && "$REPOSITORY_URL" != *$'\n'* ]] \
    || fail "Invalid repository URL"
[[ "$REPOSITORY_REF" =~ ^[A-Za-z0-9._/-]{1,128}$ ]] || fail "Invalid repository ref"

if [[ -z "$SOURCE_DIR" ]]; then
    [[ -n "$REPOSITORY_URL" ]] || fail "Repository URL is not configured"
    TEMP_DIR="$(mktemp -d)"
    log "Downloading source revision $REPOSITORY_REF"
    git clone --depth 1 --branch "$REPOSITORY_REF" -- "$REPOSITORY_URL" "$TEMP_DIR/source"
    SOURCE_DIR="$TEMP_DIR/source"
fi
[[ -f "$SOURCE_DIR/backend/app/main.py" && -f "$SOURCE_DIR/frontend/package-lock.json" ]] \
    || fail "Invalid source tree"

log "Updating host runtime dependencies"
npm install --global --omit=dev "pm2@$PM2_VERSION"

TEMP_DIR="${TEMP_DIR:-$(mktemp -d)}"
STAGING_DIR="$TEMP_DIR/staging"
mkdir -p "$STAGING_DIR"
rsync -a --delete \
    --exclude='.git' --exclude='.venv' --exclude='node_modules' \
    --exclude='.nuxt' --exclude='.output' --exclude='*.db' \
    "$SOURCE_DIR/" "$STAGING_DIR/"

log "Building frontend"
(
    cd "$STAGING_DIR/frontend"
    npm ci
    npm run typecheck
    npm test -- --run
    npm run build
)

timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
set -a
# shellcheck disable=SC1090,SC1091
source "$CONFIG_DIR/panel.env"
set +a
BACKEND_PORT="${BACKEND_PORT:-8000}"
FRONTEND_PORT="${FRONTEND_PORT:-3000}"
AGENT_PORT="${AGENT_PORT:-8090}"
ADMINER_PORT="${ADMINER_PORT:-8081}"
for port in "$BACKEND_PORT" "$FRONTEND_PORT" "$AGENT_PORT" "$ADMINER_PORT"; do
    if ! [[ "$port" =~ ^[0-9]+$ ]] || ! ((port >= 1024 && port <= 65535)); then
        fail "Internal ports must be between 1024 and 65535"
    fi
done
BACKUP_DIR="$STORAGE_ROOT/backups/full/panel-update-$timestamp"
HOST_BACKUP_DIR="$TEMP_DIR/host-files"
mkdir -p "$HOST_BACKUP_DIR"
backup_host_file() {
    local source="$1"
    local name="$2"
    if [[ -f "$source" ]]; then
        cp -a "$source" "$HOST_BACKUP_DIR/$name"
    else
        : > "$HOST_BACKUP_DIR/$name.absent"
    fi
}
backup_host_file /etc/systemd/system/vps-panel-agent.service agent-service
backup_host_file /usr/local/libexec/nectarine-agent-helper helper
backup_host_file /etc/sudoers.d/nectarine-agent sudoers
backup_host_file "$CONFIG_DIR/agent.env" agent-env
install -d -m 0750 -o "$PANEL_USER" -g "$PANEL_USER" "$BACKUP_DIR"
runuser -u postgres -- pg_dump --format=custom nectarine > "$BACKUP_DIR/panel-db.dump"
chmod 0640 "$BACKUP_DIR/panel-db.dump"
tar -C "$INSTALL_DIR" \
    --exclude='.venv' --exclude='frontend/node_modules' \
    -czf "$BACKUP_DIR/application.tar.gz" .
cp "$CONFIG_DIR/panel.env" "$BACKUP_DIR/panel.env"
chmod 0600 "$BACKUP_DIR/panel.env"

log "Updating Python dependencies"
"$INSTALL_DIR/.venv/bin/pip" install \
    -r "$STAGING_DIR/backend/requirements.txt" \
    -r "$STAGING_DIR/worker/requirements.txt" \
    -r "$STAGING_DIR/telegram-bot/requirements.txt" \
    -r "$STAGING_DIR/agent/requirements.txt"

active_services=()
for service in "${SERVICES[@]}"; do
    if systemctl is-active --quiet "$service"; then
        active_services+=("$service")
    fi
done
if ((${#active_services[@]})); then
    systemctl stop "${active_services[@]}"
fi

rollback_application() {
    log "Restoring previous application files"
    find "$INSTALL_DIR" -mindepth 1 -maxdepth 1 \
        ! -name '.venv' -exec rm -rf -- {} +
    tar -C "$INSTALL_DIR" -xzf "$BACKUP_DIR/application.tar.gz"
    chown -R root:root "$INSTALL_DIR"
    restore_host_file() {
        local name="$1"
        local target="$2"
        if [[ -f "$HOST_BACKUP_DIR/$name.absent" ]]; then
            rm -f "$target"
        else
            cp -a "$HOST_BACKUP_DIR/$name" "$target"
        fi
    }
    restore_host_file agent-service /etc/systemd/system/vps-panel-agent.service
    restore_host_file helper /usr/local/libexec/nectarine-agent-helper
    restore_host_file sudoers /etc/sudoers.d/nectarine-agent
    restore_host_file agent-env "$CONFIG_DIR/agent.env"
    systemctl daemon-reload
    if ((${#active_services[@]})); then
        systemctl start "${active_services[@]}" || true
    fi
}
trap 'rollback_application' ERR

rsync -a --delete \
    --exclude='.venv' --exclude='frontend/node_modules' \
    "$STAGING_DIR/" "$INSTALL_DIR/"
chown -R root:root "$INSTALL_DIR"

sed \
    -e "s|@AGENT_TOKEN@|${AGENT_TOKEN//&/\\&}|g" \
    -e "s|@STORAGE_ROOT@|${STORAGE_ROOT//&/\\&}|g" \
    "$INSTALL_DIR/installer/templates/agent.env" \
    > "$CONFIG_DIR/agent.env.tmp"
install -m 0640 -o root -g "$AGENT_USER" \
    "$CONFIG_DIR/agent.env.tmp" "$CONFIG_DIR/agent.env"
rm -f "$CONFIG_DIR/agent.env.tmp"

install -d -m 0755 /usr/local/libexec
sed "s|@INSTALL_DIR@|$INSTALL_DIR|g" \
    "$INSTALL_DIR/installer/helpers/nectarine-agent-helper" \
    > /usr/local/libexec/nectarine-agent-helper
chown root:root /usr/local/libexec/nectarine-agent-helper
chmod 0755 /usr/local/libexec/nectarine-agent-helper
sed "s|@AGENT_USER@|$AGENT_USER|g" \
    "$INSTALL_DIR/installer/sudoers/nectarine-agent" \
    > /etc/sudoers.d/nectarine-agent
chown root:root /etc/sudoers.d/nectarine-agent
chmod 0440 /etc/sudoers.d/nectarine-agent
visudo -cf /etc/sudoers.d/nectarine-agent

(
    cd "$INSTALL_DIR/backend"
    runuser -u "$PANEL_USER" --preserve-environment -- \
        env HOME="$INSTALL_DIR" PGSSLMODE=disable \
        "$INSTALL_DIR/.venv/bin/alembic" upgrade head
)

storage_root="$(sed -n 's/^STORAGE_ROOT=//p' "$CONFIG_DIR/panel.env")"
[[ "$storage_root" == /* && "$storage_root" != / ]] || fail "Invalid storage root"
install -d -m 0750 -o "$PANEL_USER" -g "$PANEL_USER" \
    "$storage_root/.minecraft-staging" "$storage_root/.minecraft-locks"
install -d -m 0700 -o root -g root "$storage_root/.minecraft-agent-locks"
render_unit() {
    local source="$1"
    local target="$2"
    sed \
        -e "s|@PANEL_USER@|$PANEL_USER|g" \
        -e "s|@AGENT_USER@|$AGENT_USER|g" \
        -e "s|@INSTALL_DIR@|$INSTALL_DIR|g" \
        -e "s|@CONFIG_DIR@|$CONFIG_DIR|g" \
        -e "s|@STORAGE_ROOT@|${storage_root//&/\\&}|g" \
        -e "s|@BACKEND_PORT@|$BACKEND_PORT|g" \
        -e "s|@FRONTEND_PORT@|$FRONTEND_PORT|g" \
        -e "s|@AGENT_PORT@|$AGENT_PORT|g" \
        "$source" > "$target"
    chmod 0644 "$target"
}
for unit in "$INSTALL_DIR"/installer/systemd/*.service; do
    render_unit "$unit" "/etc/systemd/system/$(basename "$unit")"
done
systemctl daemon-reload
systemctl enable "${SERVICES[@]}"
systemctl start "${SERVICES[@]}"

for _ in {1..30}; do
    if curl -fsS "http://127.0.0.1:$BACKEND_PORT/api/v1/health" >/dev/null \
        && curl -fsS "http://127.0.0.1:$FRONTEND_PORT/" >/dev/null; then
        break
    fi
    sleep 1
done
curl -fsS "http://127.0.0.1:$BACKEND_PORT/api/v1/health" >/dev/null
curl -fsS "http://127.0.0.1:$FRONTEND_PORT/" >/dev/null
for service in "${SERVICES[@]}"; do
    systemctl is-active --quiet "$service"
done

docker pull "$ADMINER_IMAGE"
docker rm --force nectarine-adminer >/dev/null 2>&1 || true
install -d -m 0770 -o "$PANEL_USER" -g "$PANEL_USER" \
    "$STORAGE_ROOT/databases/sqlite"
docker run --detach \
    --name nectarine-adminer \
    --restart unless-stopped \
    --security-opt no-new-privileges:true \
    --cap-drop ALL \
    --group-add "$(id -g "$PANEL_USER")" \
    --publish "127.0.0.1:$ADMINER_PORT:8080" \
    --volume "$STORAGE_ROOT/databases/sqlite:$STORAGE_ROOT/databases/sqlite" \
    "$ADMINER_IMAGE" >/dev/null
trap - ERR

printf 'NECTARINE_REPOSITORY_URL=%s\nNECTARINE_REPOSITORY_REF=%s\n' \
    "$REPOSITORY_URL" "$REPOSITORY_REF" > "$CONFIG_DIR/source.env"
chmod 0600 "$CONFIG_DIR/source.env"
log "Update complete. Recovery backup: $BACKUP_DIR"
