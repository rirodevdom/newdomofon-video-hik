#!/usr/bin/env bash
set -Eeuo pipefail

SOURCE_DIR="${SOURCE_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
PROJECT_DIR="${PROJECT_DIR:-/opt/newdomofon-video-hik}"
ENV_FILE="${ENV_FILE:-/etc/newdomofon-video-hik/app.env}"
BACKUP_ROOT="${BACKUP_ROOT:-/opt/newdomofon-video-hik-backups}"
STAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP_DIR="$BACKUP_ROOT/update-$STAMP"
SERVICE_NAME="newdomofon-video-hik.service"
SERVICE_STOPPED=0

fail() { echo "ERROR: $*" >&2; exit 1; }
log() { printf '\n[%s] %s\n' "$(date '+%F %T')" "$*"; }

recover_service_on_failure() {
  local status=$?
  trap - EXIT
  if (( status != 0 && SERVICE_STOPPED == 1 )); then
    echo >&2
    echo "Update failed; attempting to restore ${SERVICE_NAME}" >&2
    systemctl daemon-reload >/dev/null 2>&1 || true
    if ! systemctl is-active --quiet "$SERVICE_NAME"; then
      systemctl start "$SERVICE_NAME" >/dev/null 2>&1 || true
    fi
    systemctl is-active "$SERVICE_NAME" 2>/dev/null || true
  fi
  exit "$status"
}
trap recover_service_on_failure EXIT

health_ready() {
  python3 -c '
import json, sys
try:
    data = json.load(sys.stdin)
except Exception:
    raise SystemExit(1)
if data.get("ok") is not True:
    raise SystemExit(1)
devices = int(data.get("devices") or 0)
channels = int(data.get("channels") or 0)
recorders = int(data.get("recorders") or 0)
paired = bool(data.get("master_pairing"))
# A paired node with a restored device must not be declared ready while its
# channel state is empty. When channels exist, wait for at least one live
# recorder so the first browser request cannot race service startup.
if paired and devices > 0 and channels <= 0:
    raise SystemExit(1)
if channels > 0 and recorders <= 0:
    raise SystemExit(1)
' 
}

[[ "$(id -u)" -eq 0 ]] || fail "Run as root"
[[ -d "$PROJECT_DIR" ]] || fail "Installed project not found: $PROJECT_DIR"
[[ -f "$ENV_FILE" ]] || fail "Environment file not found: $ENV_FILE"

install -d -m 0750 "$BACKUP_DIR"
cp -a "$ENV_FILE" "$BACKUP_DIR/app.env"
[[ -f /var/lib/newdomofon-video-hik/state.enc.json ]] \
  && cp -a /var/lib/newdomofon-video-hik/state.enc.json "$BACKUP_DIR/state.enc.json" || true

log "Stopping service"
systemctl stop "$SERVICE_NAME"
SERVICE_STOPPED=1

log "Updating project from archive directory"
rsync -a --delete \
  --exclude '.git/' \
  --exclude 'node_modules/' \
  --exclude 'dist/' \
  "$SOURCE_DIR/" "$PROJECT_DIR/"

cd "$PROJECT_DIR"
gzip -dc package-lock.json.gz > package-lock.json
npm ci --include=dev

# The application config validates node credentials at module-import time.
# Load the installed environment for checks while keeping tests independently
# runnable with their own defaults.
set -a
. "$ENV_FILE"
set +a
npm run check

npm prune --omit=dev
chown -R root:root "$PROJECT_DIR"

install -m 0644 "$PROJECT_DIR/deploy/systemd/newdomofon-video-hik.service" /etc/systemd/system/newdomofon-video-hik.service
systemctl daemon-reload
systemctl restart "$SERVICE_NAME"

HEALTH_PORT="${HIK_NODE_PORT:-3020}"
LAST_HEALTH=''
for _ in $(seq 1 60); do
  LAST_HEALTH="$(curl -fsS "http://127.0.0.1:${HEALTH_PORT}/health" 2>/dev/null || true)"
  if [[ -n "$LAST_HEALTH" ]] && health_ready <<<"$LAST_HEALTH"; then
    printf '%s\n' "$LAST_HEALTH"
    SERVICE_STOPPED=0
    log "Update completed; channels and recorders recovered; backup: $BACKUP_DIR"
    exit 0
  fi
  sleep 1
done

printf 'Last health response: %s\n' "${LAST_HEALTH:-<none>}" >&2
journalctl -u "$SERVICE_NAME" -n 160 --no-pager
fail "Updated service started but did not recover channels and recorders"
