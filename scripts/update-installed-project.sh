#!/usr/bin/env bash
set -Eeuo pipefail

SOURCE_DIR="${SOURCE_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
PROJECT_DIR="${PROJECT_DIR:-/opt/newdomofon-video-hik}"
ENV_FILE="${ENV_FILE:-/etc/newdomofon-video-hik/app.env}"
BACKUP_ROOT="${BACKUP_ROOT:-/opt/newdomofon-video-hik-backups}"
STAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP_DIR="$BACKUP_ROOT/update-$STAMP"

fail() { echo "ERROR: $*" >&2; exit 1; }
log() { printf '\n[%s] %s\n' "$(date '+%F %T')" "$*"; }

[[ "$(id -u)" -eq 0 ]] || fail "Run as root"
[[ -d "$PROJECT_DIR" ]] || fail "Installed project not found: $PROJECT_DIR"
[[ -f "$ENV_FILE" ]] || fail "Environment file not found: $ENV_FILE"

install -d -m 0750 "$BACKUP_DIR"
cp -a "$ENV_FILE" "$BACKUP_DIR/app.env"
[[ -f /var/lib/newdomofon-video-hik/state.enc.json ]] \
  && cp -a /var/lib/newdomofon-video-hik/state.enc.json "$BACKUP_DIR/state.enc.json" || true

log "Stopping service"
systemctl stop newdomofon-video-hik.service

log "Updating project from archive directory"
rsync -a --delete \
  --exclude '.git/' \
  --exclude 'node_modules/' \
  --exclude 'dist/' \
  "$SOURCE_DIR/" "$PROJECT_DIR/"

cd "$PROJECT_DIR"
gzip -dc package-lock.json.gz > package-lock.json
npm ci --include=dev
npm run check
npm prune --omit=dev
chown -R root:root "$PROJECT_DIR"

install -m 0644 "$PROJECT_DIR/deploy/systemd/newdomofon-video-hik.service" /etc/systemd/system/newdomofon-video-hik.service
systemctl daemon-reload
systemctl restart newdomofon-video-hik.service

set -a
. "$ENV_FILE"
set +a
HEALTH_PORT="${HIK_NODE_PORT:-3020}"
for _ in $(seq 1 30); do
  if curl -fsS "http://127.0.0.1:${HEALTH_PORT}/health"; then
    echo
    log "Update completed; backup: $BACKUP_DIR"
    exit 0
  fi
  sleep 1
done

journalctl -u newdomofon-video-hik.service -n 120 --no-pager
fail "Updated service did not become healthy"
