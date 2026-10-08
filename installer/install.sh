#!/usr/bin/env bash
set -Eeuo pipefail

readonly PANEL_USER="vps-panel"
readonly AGENT_USER="vps-panel-agent"
readonly INSTALL_DIR="/opt/nectarine-panel"
readonly CONFIG_DIR="/etc/nectarine-panel"
readonly LOCK_FILE="/run/lock/nectarine-panel-installer.lock"
readonly DEFAULT_REPOSITORY_URL="https://github.com/JanekDeveloper/NectarinePanel.git"
readonly ADMINER_IMAGE="adminer:5.4.2-standalone"
readonly PM2_VERSION="7.0.1"

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_DIR="${NECTARINE_SOURCE_DIR:-}"
REPOSITORY_URL="${NECTARINE_REPOSITORY_URL:-$DEFAULT_REPOSITORY_URL}"
REPOSITORY_REF="${NECTARINE_REPOSITORY_REF:-main}"
PANEL_DOMAIN="${PANEL_DOMAIN:-}"
LETSENCRYPT_EMAIL="${LETSENCRYPT_EMAIL:-}"
ADMIN_USERNAME="${ADMIN_USERNAME:-admin}"
ADMIN_PASSWORD="${ADMIN_PASSWORD:-}"
STORAGE_ROOT="${STORAGE_ROOT:-}"
TELEGRAM_BOT_TOKEN="${TELEGRAM_BOT_TOKEN:-}"
TELEGRAM_OWNER_ID="${TELEGRAM_OWNER_ID:-}"
PUBLIC_IP="${PUBLIC_IP:-}"
MAX_UPLOAD_MB="${MAX_UPLOAD_MB:-512}"
BACKEND_PORT="${BACKEND_PORT:-8000}"
FRONTEND_PORT="${FRONTEND_PORT:-3000}"
AGENT_PORT="${AGENT_PORT:-8090}"
ADMINER_PORT="${ADMINER_PORT:-8081}"
NON_INTERACTIVE=false
DRY_RUN=false
SKIP_SSL=false
TEMP_DIR=""

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

usage() {
    cat <<'EOF'
Usage: install.sh [options]

  --domain DOMAIN
  --email EMAIL
  --admin-user USER
  --admin-password PASSWORD
  --storage-root PATH
  --telegram-token TOKEN
  --telegram-owner-id ID
  --public-ip ADDRESS
  --source PATH
  --repository URL
  --ref GIT_REF
  --max-upload-mb NUMBER
  --backend-port PORT
  --frontend-port PORT
  --agent-port PORT
  --adminer-port PORT
  --non-interactive
  --skip-ssl
  --dry-run
EOF
}

while (($#)); do
    case "$1" in
        --domain) PANEL_DOMAIN="${2:?Missing domain}"; shift 2 ;;
        --email) LETSENCRYPT_EMAIL="${2:?Missing email}"; shift 2 ;;
        --admin-user) ADMIN_USERNAME="${2:?Missing username}"; shift 2 ;;
        --admin-password) ADMIN_PASSWORD="${2:?Missing password}"; shift 2 ;;
        --storage-root) STORAGE_ROOT="${2:?Missing storage path}"; shift 2 ;;
        --telegram-token) TELEGRAM_BOT_TOKEN="${2:?Missing token}"; shift 2 ;;
        --telegram-owner-id) TELEGRAM_OWNER_ID="${2:?Missing owner ID}"; shift 2 ;;
        --public-ip) PUBLIC_IP="${2:?Missing address}"; shift 2 ;;
        --source) SOURCE_DIR="${2:?Missing source path}"; shift 2 ;;
        --repository) REPOSITORY_URL="${2:?Missing repository URL}"; shift 2 ;;
        --ref) REPOSITORY_REF="${2:?Missing git ref}"; shift 2 ;;
        --max-upload-mb) MAX_UPLOAD_MB="${2:?Missing upload limit}"; shift 2 ;;
        --backend-port) BACKEND_PORT="${2:?Missing backend port}"; shift 2 ;;
        --frontend-port) FRONTEND_PORT="${2:?Missing frontend port}"; shift 2 ;;
        --agent-port) AGENT_PORT="${2:?Missing agent port}"; shift 2 ;;
        --adminer-port) ADMINER_PORT="${2:?Missing Adminer port}"; shift 2 ;;
        --non-interactive) NON_INTERACTIVE=true; shift ;;
        --skip-ssl) SKIP_SSL=true; shift ;;
        --dry-run) DRY_RUN=true; shift ;;
        --help|-h) usage; exit 0 ;;
        *) fail "Unknown argument: $1" ;;
    esac
done

if [[ -r "$CONFIG_DIR/panel.env" ]]; then
    existing_value() {
        local key="$1"
        sed -n "s/^${key}=//p" "$CONFIG_DIR/panel.env" | tail -n 1
    }
    PANEL_DOMAIN="${PANEL_DOMAIN:-$(existing_value PANEL_DOMAIN)}"
    LETSENCRYPT_EMAIL="${LETSENCRYPT_EMAIL:-$(existing_value LETSENCRYPT_EMAIL)}"
    STORAGE_ROOT="${STORAGE_ROOT:-$(existing_value STORAGE_ROOT)}"
    TELEGRAM_BOT_TOKEN="${TELEGRAM_BOT_TOKEN:-$(existing_value TELEGRAM_BOT_TOKEN)}"
    TELEGRAM_OWNER_ID="${TELEGRAM_OWNER_ID:-$(existing_value TELEGRAM_OWNER_ID)}"
    PUBLIC_IP="${PUBLIC_IP:-$(existing_value PUBLIC_IP)}"
fi

prompt_value() {
    local variable_name="$1"
    local prompt="$2"
    local default_value="${3:-}"
    local current_value="${!variable_name}"
    if [[ -n "$current_value" || "$NON_INTERACTIVE" == true ]]; then
        return
    fi
    read -r -p "$prompt${default_value:+ [$default_value]}: " current_value
    printf -v "$variable_name" '%s' "${current_value:-$default_value}"
}

prompt_value PANEL_DOMAIN "Panel domain"
prompt_value LETSENCRYPT_EMAIL "Let's Encrypt email"
prompt_value ADMIN_USERNAME "Admin username" "admin"
STORAGE_ROOT="${STORAGE_ROOT:-/srv/vps-panel}"
prompt_value STORAGE_ROOT "Storage root" "/srv/vps-panel"
if [[ -z "$TELEGRAM_BOT_TOKEN" && "$NON_INTERACTIVE" == false ]]; then
    read -r -p "Telegram bot token (optional): " TELEGRAM_BOT_TOKEN
fi
if [[ -z "$TELEGRAM_OWNER_ID" && -n "$TELEGRAM_BOT_TOKEN" && "$NON_INTERACTIVE" == false ]]; then
    read -r -p "Telegram owner ID: " TELEGRAM_OWNER_ID
fi
if [[ -z "$ADMIN_PASSWORD" && "$NON_INTERACTIVE" == false ]]; then
    read -r -s -p "Admin password (empty to generate): " ADMIN_PASSWORD
    printf '\n'
fi

[[ "$PANEL_DOMAIN" =~ ^([a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,63}$ ]] \
    || fail "Invalid panel domain"
if [[ "$SKIP_SSL" == false ]]; then
    [[ "$LETSENCRYPT_EMAIL" =~ ^[^[:space:]@]+@[^[:space:]@]+\.[^[:space:]@]+$ ]] \
        || fail "A valid Let's Encrypt email is required"
fi
[[ "$ADMIN_USERNAME" =~ ^[a-zA-Z0-9_.-]{3,64}$ ]] || fail "Invalid admin username"
[[ "$STORAGE_ROOT" =~ ^/[A-Za-z0-9._/-]+$ ]] \
    || fail "Storage root must be a safe absolute path"
if ! [[ "$MAX_UPLOAD_MB" =~ ^[0-9]+$ ]] || ! ((MAX_UPLOAD_MB >= 1 && MAX_UPLOAD_MB <= 4096)); then
    fail "Upload limit must be between 1 and 4096 MiB"
fi
for port in "$BACKEND_PORT" "$FRONTEND_PORT" "$AGENT_PORT" "$ADMINER_PORT"; do
    if ! [[ "$port" =~ ^[0-9]+$ ]] || ! ((port >= 1024 && port <= 65535)); then
        fail "Internal ports must be between 1024 and 65535"
    fi
done
if [[ "$BACKEND_PORT" == "$FRONTEND_PORT" || "$BACKEND_PORT" == "$AGENT_PORT" \
    || "$BACKEND_PORT" == "$ADMINER_PORT" || "$FRONTEND_PORT" == "$AGENT_PORT" \
    || "$FRONTEND_PORT" == "$ADMINER_PORT" || "$AGENT_PORT" == "$ADMINER_PORT" ]]; then
    fail "Internal ports must be unique"
fi
[[ "$REPOSITORY_URL" != *[[:space:]]* && "$REPOSITORY_URL" != *$'\n'* ]] \
    || fail "Invalid repository URL"
[[ "$REPOSITORY_REF" =~ ^[A-Za-z0-9._/-]{1,128}$ ]] || fail "Invalid repository ref"
if [[ -n "$TELEGRAM_OWNER_ID" ]]; then
    [[ "$TELEGRAM_OWNER_ID" =~ ^[0-9]{1,20}$ ]] || fail "Invalid Telegram owner ID"
fi
if [[ -n "$TELEGRAM_BOT_TOKEN" ]]; then
    [[ "$TELEGRAM_BOT_TOKEN" =~ ^[0-9]{6,15}:[A-Za-z0-9_-]{20,}$ ]] \
        || fail "Invalid Telegram bot token"
    [[ -n "$TELEGRAM_OWNER_ID" ]] || fail "Telegram owner ID is required with a bot token"
fi
if [[ -n "$ADMIN_PASSWORD" && ${#ADMIN_PASSWORD} -lt 12 ]]; then
    fail "Admin password must contain at least 12 characters"
fi

if [[ "$DRY_RUN" == true ]]; then
    log "Dry-run validation"
    for script in "$SCRIPT_DIR"/*.sh; do
        bash -n "$script"
    done
    sh -n "$SCRIPT_DIR/helpers/nectarine-agent-helper"
    for required in \
        "$SCRIPT_DIR/templates/panel.env" \
        "$SCRIPT_DIR/../agent/system_agent/assets/spigot.Dockerfile" \
        "$SCRIPT_DIR/templates/agent.env" \
        "$SCRIPT_DIR/helpers/nectarine-agent-helper" \
        "$SCRIPT_DIR/sudoers/nectarine-agent" \
        "$SCRIPT_DIR/nginx/nectarine-panel.conf" \
        "$SCRIPT_DIR/systemd/vps-panel-agent.service" \
        "$SCRIPT_DIR/systemd/vps-panel-backend.service" \
        "$SCRIPT_DIR/systemd/vps-panel-frontend.service" \
        "$SCRIPT_DIR/systemd/vps-panel-worker.service" \
        "$SCRIPT_DIR/systemd/vps-panel-scheduler.service" \
        "$SCRIPT_DIR/systemd/vps-panel-telegram-bot.service"; do
        [[ -f "$required" ]] || fail "Missing installer asset: $required"
    done
    log "Dry-run successful for $PANEL_DOMAIN"
    exit 0
fi

[[ "$(id -u)" -eq 0 ]] || fail "Run the installer as root"
exec 9>"$LOCK_FILE"
flock -n 9 || fail "Another installer process is running"

[[ -r /etc/os-release ]] || fail "Cannot detect the operating system"
# shellcheck disable=SC1091
source /etc/os-release
[[ "${ID:-}" == "ubuntu" && "${VERSION_ID:-}" == "24.04" ]] \
    || fail "Ubuntu 24.04 LTS is required"

export DEBIAN_FRONTEND=noninteractive
log "Installing system packages"
apt-get update
apt-get \
    -o Dpkg::Options::=--force-confdef \
    -o Dpkg::Options::=--force-confold \
    install -y --no-install-recommends \
    ca-certificates curl gpg git rsync openssl jq \
    sudo \
    python3 python3-venv python3-pip \
    nginx certbot python3-certbot-nginx \
    postgresql postgresql-client redis-server \
    unzip zip mariadb-client

install -m 0755 -d /etc/apt/keyrings
if [[ ! -f /etc/apt/keyrings/nodesource.gpg ]]; then
    curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key \
        | gpg --dearmor -o /etc/apt/keyrings/nodesource.gpg
fi
printf '%s\n' \
    "deb [signed-by=/etc/apt/keyrings/nodesource.gpg] https://deb.nodesource.com/node_22.x nodistro main" \
    > /etc/apt/sources.list.d/nodesource.list

if [[ ! -f /etc/apt/keyrings/docker.asc ]]; then
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
        -o /etc/apt/keyrings/docker.asc
    chmod a+r /etc/apt/keyrings/docker.asc
fi
architecture="$(dpkg --print-architecture)"
printf '%s\n' \
    "deb [arch=$architecture signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $VERSION_CODENAME stable" \
    > /etc/apt/sources.list.d/docker.list
apt-get update
apt-get \
    -o Dpkg::Options::=--force-confdef \
    -o Dpkg::Options::=--force-confold \
    install -y --no-install-recommends \
    nodejs docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
npm install --global --omit=dev "pm2@$PM2_VERSION"
systemctl enable postgresql redis-server docker nginx
systemctl start postgresql redis-server nginx
systemctl reset-failed docker >/dev/null 2>&1 || true
for _ in {1..5}; do
    if systemctl start docker && docker info >/dev/null 2>&1; then
        break
    fi
    systemctl reset-failed docker >/dev/null 2>&1 || true
    sleep 3
done
docker info >/dev/null 2>&1 || fail "Docker daemon is not available"

if ! id "$PANEL_USER" >/dev/null 2>&1; then
    useradd --system --home-dir "$INSTALL_DIR" --shell /usr/sbin/nologin "$PANEL_USER"
fi
if ! id "$AGENT_USER" >/dev/null 2>&1; then
    useradd --system --user-group --home-dir "$INSTALL_DIR/agent" \
        --shell /usr/sbin/nologin "$AGENT_USER"
fi

if [[ -z "$SOURCE_DIR" ]]; then
    candidate="$(cd -- "$SCRIPT_DIR/.." && pwd)"
    if [[ -f "$candidate/backend/app/main.py" && -f "$candidate/frontend/package.json" ]]; then
        SOURCE_DIR="$candidate"
    fi
fi
if [[ -z "$SOURCE_DIR" || ! -f "$SOURCE_DIR/backend/app/main.py" ]]; then
    TEMP_DIR="$(mktemp -d)"
    log "Downloading source revision $REPOSITORY_REF"
    git clone --depth 1 --branch "$REPOSITORY_REF" -- "$REPOSITORY_URL" "$TEMP_DIR/source"
    SOURCE_DIR="$TEMP_DIR/source"
fi
[[ -f "$SOURCE_DIR/frontend/package-lock.json" ]] || fail "Invalid source tree"

log "Installing application files"
install -d -m 0755 "$INSTALL_DIR"
rsync -a --delete \
    --exclude='.git' --exclude='.venv' --exclude='node_modules' \
    --exclude='.nuxt' --exclude='.output' --exclude='*.db' \
    "$SOURCE_DIR/" "$INSTALL_DIR/"
chown -R root:root "$INSTALL_DIR"
find "$INSTALL_DIR" -type d -exec chmod u=rwx,go=rx {} +

install -d -m 0750 -o "$PANEL_USER" -g "$PANEL_USER" \
    "$STORAGE_ROOT" \
    "$STORAGE_ROOT/projects" \
    "$STORAGE_ROOT/minecraft" \
    "$STORAGE_ROOT/.minecraft-staging" \
    "$STORAGE_ROOT/.minecraft-locks" \
    "$STORAGE_ROOT/backups/projects" \
    "$STORAGE_ROOT/backups/databases" \
    "$STORAGE_ROOT/backups/full" \
    "$STORAGE_ROOT/imports" \
    "$STORAGE_ROOT/state"
install -d -m 0700 -o root -g root "$STORAGE_ROOT/.minecraft-agent-locks"
install -d -m 0770 -o "$PANEL_USER" -g "$PANEL_USER" \
    "$STORAGE_ROOT/databases/sqlite"
install -d -m 0755 /var/lib/vps-panel/acme /etc/nginx/vps-panel
install -d -m 0750 "$CONFIG_DIR"

if docker container inspect nectarine-adminer >/dev/null 2>&1; then
    docker rm --force nectarine-adminer >/dev/null
fi
docker pull "$ADMINER_IMAGE"
docker run --detach \
    --name nectarine-adminer \
    --restart unless-stopped \
    --security-opt no-new-privileges:true \
    --cap-drop ALL \
    --group-add "$(id -g "$PANEL_USER")" \
    --publish "127.0.0.1:$ADMINER_PORT:8080" \
    --volume "$STORAGE_ROOT/databases/sqlite:$STORAGE_ROOT/databases/sqlite" \
    "$ADMINER_IMAGE" >/dev/null

log "Installing Python and frontend dependencies"
python3 -m venv "$INSTALL_DIR/.venv"
"$INSTALL_DIR/.venv/bin/pip" install --upgrade pip
"$INSTALL_DIR/.venv/bin/pip" install \
    -r "$INSTALL_DIR/backend/requirements.txt" \
    -r "$INSTALL_DIR/worker/requirements.txt" \
    -r "$INSTALL_DIR/telegram-bot/requirements.txt" \
    -r "$INSTALL_DIR/agent/requirements.txt"
(
    cd "$INSTALL_DIR/frontend"
    npm ci
    npm run build
)
chown -R root:root "$INSTALL_DIR"

random_hex() {
    openssl rand -hex "${1:-32}"
}

existing_value() {
    local key="$1"
    if [[ -r "$CONFIG_DIR/panel.env" ]]; then
        sed -n "s/^${key}=//p" "$CONFIG_DIR/panel.env" | tail -n 1
    fi
}

DATABASE_PASSWORD="$(existing_value PANEL_DB_PASSWORD)"
JWT_SECRET="$(existing_value JWT_SECRET)"
FIELD_ENCRYPTION_KEY="$(existing_value FIELD_ENCRYPTION_KEY)"
AGENT_TOKEN="$(existing_value AGENT_TOKEN)"
TELEGRAM_API_TOKEN="$(existing_value TELEGRAM_API_TOKEN)"
DATABASE_PASSWORD="${DATABASE_PASSWORD:-$(random_hex 24)}"
JWT_SECRET="${JWT_SECRET:-$(random_hex 32)}"
FIELD_ENCRYPTION_KEY="${FIELD_ENCRYPTION_KEY:-$(openssl rand -base64 32 | tr '+/' '-_')}"
AGENT_TOKEN="${AGENT_TOKEN:-$(random_hex 32)}"
TELEGRAM_API_TOKEN="${TELEGRAM_API_TOKEN:-$(random_hex 32)}"
ADMIN_PASSWORD_GENERATED=false
if [[ -z "$ADMIN_PASSWORD" ]]; then
    ADMIN_PASSWORD="$(random_hex 12)"
    ADMIN_PASSWORD_GENERATED=true
fi
if [[ -z "$PUBLIC_IP" ]]; then
    PUBLIC_IP="$(curl -4 -fsS --max-time 5 https://api.ipify.org || true)"
fi
[[ "$PUBLIC_IP" =~ ^[0-9a-fA-F:.]+$ ]] || fail "Unable to determine a valid public IP"

log "Configuring PostgreSQL"
runuser -u postgres -- psql --set=ON_ERROR_STOP=1 \
    --set=db_password="$DATABASE_PASSWORD" <<'SQL'
SELECT format('CREATE ROLE nectarine LOGIN PASSWORD %L', :'db_password')
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'nectarine')\gexec
ALTER ROLE nectarine WITH LOGIN PASSWORD :'db_password';
SELECT 'CREATE DATABASE nectarine OWNER nectarine'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'nectarine')\gexec
SQL

escape_sed() {
    printf '%s' "$1" | sed 's/[&|]/\\&/g'
}

render_panel_env() {
    sed \
        -e "s|@PANEL_DOMAIN@|$(escape_sed "$PANEL_DOMAIN")|g" \
        -e "s|@DATABASE_PASSWORD@|$(escape_sed "$DATABASE_PASSWORD")|g" \
        -e "s|@JWT_SECRET@|$(escape_sed "$JWT_SECRET")|g" \
        -e "s|@FIELD_ENCRYPTION_KEY@|$(escape_sed "$FIELD_ENCRYPTION_KEY")|g" \
        -e "s|@AGENT_TOKEN@|$(escape_sed "$AGENT_TOKEN")|g" \
        -e "s|@PUBLIC_IP@|$(escape_sed "$PUBLIC_IP")|g" \
        -e "s|@LETSENCRYPT_EMAIL@|$(escape_sed "$LETSENCRYPT_EMAIL")|g" \
        -e "s|@STORAGE_ROOT@|$(escape_sed "$STORAGE_ROOT")|g" \
        -e "s|@TELEGRAM_BOT_TOKEN@|$(escape_sed "$TELEGRAM_BOT_TOKEN")|g" \
        -e "s|@TELEGRAM_OWNER_ID@|$(escape_sed "$TELEGRAM_OWNER_ID")|g" \
        -e "s|@TELEGRAM_API_TOKEN@|$(escape_sed "$TELEGRAM_API_TOKEN")|g" \
        -e "s|@BACKEND_PORT@|$BACKEND_PORT|g" \
        -e "s|@FRONTEND_PORT@|$FRONTEND_PORT|g" \
        -e "s|@AGENT_PORT@|$AGENT_PORT|g" \
        -e "s|@ADMINER_PORT@|$ADMINER_PORT|g" \
        "$INSTALL_DIR/installer/templates/panel.env"
}
render_panel_env > "$CONFIG_DIR/panel.env.tmp"
install -m 0640 -o root -g "$PANEL_USER" \
    "$CONFIG_DIR/panel.env.tmp" "$CONFIG_DIR/panel.env"
rm -f "$CONFIG_DIR/panel.env.tmp"
sed \
    -e "s|@AGENT_TOKEN@|$(escape_sed "$AGENT_TOKEN")|g" \
    -e "s|@STORAGE_ROOT@|$(escape_sed "$STORAGE_ROOT")|g" \
    "$INSTALL_DIR/installer/templates/agent.env" \
    > "$CONFIG_DIR/agent.env.tmp"
install -m 0640 -o root -g "$AGENT_USER" \
    "$CONFIG_DIR/agent.env.tmp" "$CONFIG_DIR/agent.env"
rm -f "$CONFIG_DIR/agent.env.tmp"
printf 'NECTARINE_REPOSITORY_URL=%s\nNECTARINE_REPOSITORY_REF=%s\n' \
    "$REPOSITORY_URL" "$REPOSITORY_REF" > "$CONFIG_DIR/source.env"
chmod 0600 "$CONFIG_DIR/source.env"

log "Applying database migrations"
set -a
# shellcheck disable=SC1090,SC1091
source "$CONFIG_DIR/panel.env"
set +a
run_panel_command() {
    runuser -u "$PANEL_USER" --preserve-environment -- \
        env \
            HOME="$INSTALL_DIR" \
            PYTHONPATH="$INSTALL_DIR/backend:$INSTALL_DIR/worker:$INSTALL_DIR/telegram-bot:$INSTALL_DIR/agent" \
            "$@"
}
(
    cd "$INSTALL_DIR/backend"
    run_panel_command "$INSTALL_DIR/.venv/bin/alembic" upgrade head
)
ADMIN_RESULT="$(
    printf '%s\n' "$ADMIN_PASSWORD" \
        | run_panel_command "$INSTALL_DIR/.venv/bin/python" \
            -m app.cli create-admin \
            --username "$ADMIN_USERNAME" --password-stdin --if-not-exists
)"

render_unit() {
    local source="$1"
    local target="$2"
    sed \
        -e "s|@PANEL_USER@|$PANEL_USER|g" \
        -e "s|@AGENT_USER@|$AGENT_USER|g" \
        -e "s|@INSTALL_DIR@|$INSTALL_DIR|g" \
        -e "s|@CONFIG_DIR@|$CONFIG_DIR|g" \
        -e "s|@STORAGE_ROOT@|$(escape_sed "$STORAGE_ROOT")|g" \
        -e "s|@BACKEND_PORT@|$BACKEND_PORT|g" \
        -e "s|@FRONTEND_PORT@|$FRONTEND_PORT|g" \
        -e "s|@AGENT_PORT@|$AGENT_PORT|g" \
        "$source" > "$target"
    chmod 0644 "$target"
}

log "Installing systemd services"
for unit in "$INSTALL_DIR"/installer/systemd/*.service; do
    render_unit "$unit" "/etc/systemd/system/$(basename "$unit")"
done

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

sed \
    -e "s|@PANEL_DOMAIN@|$(escape_sed "$PANEL_DOMAIN")|g" \
    -e "s|@MAX_UPLOAD_MB@|$MAX_UPLOAD_MB|g" \
    -e "s|@BACKEND_PORT@|$BACKEND_PORT|g" \
    -e "s|@FRONTEND_PORT@|$FRONTEND_PORT|g" \
    -e "s|@ADMINER_PORT@|$ADMINER_PORT|g" \
    "$INSTALL_DIR/installer/nginx/nectarine-panel.conf" \
    > /etc/nginx/sites-available/nectarine-panel.conf
ln -sfn /etc/nginx/sites-available/nectarine-panel.conf \
    /etc/nginx/sites-enabled/nectarine-panel.conf
rm -f /etc/nginx/sites-enabled/default
printf 'include /etc/nginx/vps-panel/*.conf;\n' \
    > /etc/nginx/conf.d/nectarine-projects.conf
nginx -t

systemctl daemon-reload
systemctl enable --now \
    vps-panel-agent.service \
    vps-panel-backend.service \
    vps-panel-frontend.service \
    vps-panel-worker.service \
    vps-panel-scheduler.service \
    vps-panel-telegram-bot.service
systemctl reload nginx

for _ in {1..30}; do
    if curl -fsS "http://127.0.0.1:$BACKEND_PORT/api/v1/health" >/dev/null; then
        break
    fi
    sleep 1
done
curl -fsS "http://127.0.0.1:$BACKEND_PORT/api/v1/health" >/dev/null \
    || fail "Backend health check failed"
curl -fsS "http://127.0.0.1:$FRONTEND_PORT/" >/dev/null \
    || fail "Frontend health check failed"

if [[ "$SKIP_SSL" == false ]]; then
    log "Requesting Let's Encrypt certificate"
    certbot --nginx \
        --domain "$PANEL_DOMAIN" \
        --email "$LETSENCRYPT_EMAIL" \
        --agree-tos --non-interactive --redirect
    nginx -t
    systemctl reload nginx
fi

log "Installation complete: https://$PANEL_DOMAIN"
log "Admin user: $ADMIN_USERNAME"
if [[ "$ADMIN_RESULT" == "Owner created" ]]; then
    if [[ "$ADMIN_PASSWORD_GENERATED" == true ]]; then
        log "Admin password: $ADMIN_PASSWORD"
        log "Store this password now; it will not be shown again."
    else
        log "Admin password: provided externally; not printed."
    fi
elif [[ "$ADMIN_PASSWORD_GENERATED" == true ]]; then
    log "Existing owner preserved; generated password was not applied."
fi
