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

set_env_default() {
  local key="$1"
  local value="$2"
  if ! grep -qE "^${key}=" "$ENV_FILE"; then
    printf '\n%s=%s\n' "$key" "$value" >>"$ENV_FILE"
    log "Runtime env default added: $key=$value"
  fi
}

migrate_env_default() {
  local key="$1"
  local old_value="$2"
  local new_value="$3"
  if grep -qE "^${key}=${old_value}$" "$ENV_FILE"; then
    sed -i -E "s|^${key}=${old_value}$|${key}=${new_value}|" "$ENV_FILE"
    log "Runtime env managed default migrated: $key=$old_value -> $new_value"
  else
    set_env_default "$key" "$new_value"
  fi
}

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
live_expected = int(data.get("live_expected") or 0)
live_ready = int(data.get("live_ready") or 0)
live_devices_expected = int(data.get("live_devices_expected") or 0)
live_devices_ready = int(data.get("live_devices_ready") or 0)
sync_errors = int(data.get("sync_errors") or 0)
paired = bool(data.get("master_pairing"))
if sync_errors > 0:
    raise SystemExit(1)
if paired and devices > 0 and channels <= 0:
    raise SystemExit(1)
if live_expected > 0 and recorders <= 0:
    raise SystemExit(1)
# Hybrid/analog DVRs can expose enabled inputs without an actual video signal.
# A no-signal input must not roll back an otherwise healthy grouped runtime.
# Require fresh media from every DVR that has expected channels, not from every
# individual physical input.
if live_devices_expected > 0 and live_devices_ready < live_devices_expected:
    raise SystemExit(1)
if live_expected > 0 and live_ready <= 0:
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
[[ -x /opt/hikvision/hcnetsdk/bin/hik-sdk-worker.bin ]] \
  && cp -a /opt/hikvision/hcnetsdk/bin/hik-sdk-worker.bin "$BACKUP_DIR/hik-sdk-worker.bin.before" || true

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
if [[ -f /opt/hikvision/hcnetsdk/include/HCNetSDK.h ]]; then
  log "Preparing bounded native archive playback pool"
  python3 "$PROJECT_DIR/scripts/patch-native-archive-concurrency.py" --project-dir "$PROJECT_DIR"

  log "Preparing native event reconciliation and persistent backfill"
  python3 "$PROJECT_DIR/scripts/patch-native-event-stack.py" --project-dir "$PROJECT_DIR"

  set_env_default HIK_NATIVE_SDK_PREFERRED true
  set_env_default HIK_NATIVE_SDK_REQUIRED true
  set_env_default HIK_NATIVE_SDK_FALLBACK false
  set_env_default HIK_SDK_WORKER /opt/hikvision/hcnetsdk/bin/hik-sdk-worker
  set_env_default HIK_SDK_CHANNEL_PROBE /opt/hikvision/hcnetsdk/bin/hik-sdk-channel-probe
  set_env_default HIK_SDK_DEVICE_WORKER /opt/hikvision/hcnetsdk/bin/hik-sdk-device-worker
  # 4 was the managed default before archive workers were isolated/sharded.
  # Preserve any operator-custom value, but migrate untouched installations to 48.
  migrate_env_default HIK_DEVICE_ARCHIVE_MAX_ACTIVE_PER_DVR 4 48
  set_env_default HIK_DEVICE_ARCHIVE_WORKER_COUNT 3
  set_env_default HIK_DEVICE_ARCHIVE_MAX_ACTIVE_PER_WORKER 16
  set_env_default HIK_SMARTYARD_ARCHIVE_MAX_BURSTS_PER_DVR 48
  set_env_default HIK_LIVE_DELETE_THRESHOLD 60
  set_env_default HIK_EVENT_SYNC_ENABLED true
  set_env_default HIK_EVENT_SYNC_SECONDS 60
  set_env_default HIK_EVENT_SYNC_OVERLAP_SECONDS 120
  set_env_default HIK_EVENT_SYNC_INITIAL_LOOKBACK_SECONDS 3600
  set_env_default HIK_EVENT_BACKFILL_ENABLED true
  set_env_default HIK_EVENT_BACKFILL_RETENTION_DAYS 30
  set_env_default HIK_EVENT_BACKFILL_CHUNK_SECONDS 21600
  set_env_default HIK_EVENT_BACKFILL_INTERVAL_SECONDS 30
  chmod 0600 "$ENV_FILE"
else
  log "HCNetSDK is not installed; native worker rebuild skipped"
fi

gzip -dc package-lock.json.gz > package-lock.json
npm ci --include=dev

set -a
. "$ENV_FILE"
set +a
npm run check

if [[ -f /opt/hikvision/hcnetsdk/include/HCNetSDK.h ]]; then
  log "Rebuilding installed native HCNetSDK workers after materialization"
  PROJECT_DIR="$PROJECT_DIR" bash "$PROJECT_DIR/scripts/rebuild-hcnet-sdk-worker.sh"
fi

npm prune --omit=dev
chown -R root:root "$PROJECT_DIR"

install -m 0644 "$PROJECT_DIR/deploy/systemd/newdomofon-video-hik.service" /etc/systemd/system/newdomofon-video-hik.service
systemctl daemon-reload
systemctl restart "$SERVICE_NAME"

HEALTH_PORT="${HIK_NODE_PORT:-3020}"
LAST_HEALTH=''
for _ in $(seq 1 90); do
  LAST_HEALTH="$(curl -fsS "http://127.0.0.1:${HEALTH_PORT}/health" 2>/dev/null || true)"
  if [[ -n "$LAST_HEALTH" ]] && health_ready <<<"$LAST_HEALTH"; then
    printf '%s\n' "$LAST_HEALTH"
    SERVICE_STOPPED=0
    log "Update completed; grouped HCNetSDK runtime, native sync and per-DVR live readiness recovered; backup: $BACKUP_DIR"
    exit 0
  fi
  sleep 1
done

printf 'Last health response: %s\n' "${LAST_HEALTH:-<none>}" >&2
if [[ -n "${DVR_NODE_TOKEN:-}" ]]; then
  echo "Recorder diagnostics:" >&2
  curl -fsS \
    -H "Authorization: Bearer ${DVR_NODE_TOKEN}" \
    "http://127.0.0.1:${HEALTH_PORT}/api/v1/control/recorders" \
    2>/dev/null | python3 -m json.tool >&2 || true
fi
journalctl -u "$SERVICE_NAME" -n 200 --no-pager
fail "Updated service started but grouped HCNetSDK runtime/sync/per-DVR live readiness did not recover"
