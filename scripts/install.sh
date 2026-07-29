#!/usr/bin/env bash
set -Eeuo pipefail

SOURCE_DIR="${SOURCE_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
PROJECT_DIR="${PROJECT_DIR:-/opt/newdomofon-video-hik}"
ENV_DIR="${ENV_DIR:-/etc/newdomofon-video-hik}"
ENV_FILE="${ENV_FILE:-$ENV_DIR/app.env}"
DATA_DIR="${DATA_DIR:-/var/lib/newdomofon-video-hik}"
SERVICE_FILE="/etc/systemd/system/newdomofon-video-hik.service"
RUNTIME_USER="${RUNTIME_USER:-newdomofon-hik}"

fail() { echo "ERROR: $*" >&2; exit 1; }
log() { printf '\n[%s] %s\n' "$(date '+%F %T')" "$*"; }

[[ "$(id -u)" -eq 0 ]] || fail "Run as root"
for command in node npm ffmpeg rsync systemctl openssl gzip; do
  command -v "$command" >/dev/null || fail "$command is required"
done

if ! id "$RUNTIME_USER" >/dev/null 2>&1; then
  useradd --system --home-dir "$DATA_DIR" --shell /usr/sbin/nologin "$RUNTIME_USER"
fi

install -d -m 0750 -o "$RUNTIME_USER" -g "$RUNTIME_USER" "$DATA_DIR" "$DATA_DIR/live" "$DATA_DIR/archive" "$DATA_DIR/tmp"
install -d -m 0755 "$PROJECT_DIR"
install -d -m 0750 "$ENV_DIR"

log "Copying project"
rsync -a --delete \
  --exclude '.git/' \
  --exclude 'node_modules/' \
  --exclude 'dist/' \
  "$SOURCE_DIR/" "$PROJECT_DIR/"

if [[ ! -f "$ENV_FILE" ]]; then
  control_token="$(openssl rand -hex 32)"
  media_secret="$(openssl rand -hex 32)"
  state_key="$(openssl rand -hex 32)"
  sed \
    -e "s/REPLACE_WITH_CONTROL_TOKEN/$control_token/" \
    -e "s/REPLACE_WITH_MEDIA_SECRET/$media_secret/" \
    -e "s/REPLACE_WITH_32_BYTE_HEX_KEY/$state_key/" \
    "$PROJECT_DIR/deploy/env/app.env.example" >"$ENV_FILE"
fi
chown root:"$RUNTIME_USER" "$ENV_FILE"
chmod 0640 "$ENV_FILE"

log "Installing dependencies and building"
cd "$PROJECT_DIR"
gzip -dc package-lock.json.gz > package-lock.json
npm ci --include=dev
npm run build
npm prune --omit=dev
chown -R root:root "$PROJECT_DIR"

install -m 0644 "$PROJECT_DIR/deploy/systemd/newdomofon-video-hik.service" "$SERVICE_FILE"
systemctl daemon-reload
systemctl enable newdomofon-video-hik.service
systemctl restart newdomofon-video-hik.service

log "Verifying service"
set -a
. "$ENV_FILE"
set +a
HEALTH_PORT="${HIK_NODE_PORT:-3020}"
for _ in $(seq 1 30); do
  if curl -fsS "http://127.0.0.1:${HEALTH_PORT}/health"; then
    echo
    systemctl --no-pager --full status newdomofon-video-hik.service | sed -n '1,24p'
    echo
    echo "Installation completed"
    echo "Environment: $ENV_FILE"
    exit 0
  fi
  sleep 1
done

journalctl -u newdomofon-video-hik.service -n 100 --no-pager
fail "Health check failed"
