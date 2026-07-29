#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

SOURCE_DIR="${SOURCE_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
PROJECT_DIR="${PROJECT_DIR:-/opt/newdomofon-video-hik}"
ENV_DIR="${ENV_DIR:-/etc/newdomofon-video-hik}"
ENV_FILE="${ENV_FILE:-$ENV_DIR/app.env}"
DATA_DIR="${DATA_DIR:-/var/lib/newdomofon-video-hik}"
REGISTRATION_FILE="${REGISTRATION_FILE:-/root/newdomofon-hik-master-registration.env}"
SERVICE_FILE="/etc/systemd/system/newdomofon-video-hik.service"
RUNTIME_USER="${RUNTIME_USER:-newdomofon-hik}"

MASTER_URL="${MASTER_URL:-}"
NODE_ID="${NODE_ID:-}"
NODE_TOKEN="${NODE_TOKEN:-}"
MEDIA_SECRET="${MEDIA_SECRET:-}"
PUBLIC_URL="${PUBLIC_URL:-}"
INTERNAL_URL="${INTERNAL_URL:-}"
NON_INTERACTIVE=false

fail() { echo "ERROR: $*" >&2; exit 1; }
log() { printf '\n[%s] %s\n' "$(date '+%F %T')" "$*"; }

usage() {
  cat <<'USAGE'
Install NewDomofon Hikvision Node before creating its record on master.

Usage:
  bash scripts/install.sh [options]

Options:
  --master-url URL       DVR_MASTER_URL
  --node-id UUID         optional; generated locally when omitted
  --node-token TOKEN     optional; generated locally when omitted
  --media-secret SECRET  optional; generated locally when omitted
  --public-url URL       master/client URL of this node, usually http://NODE_IP:3020
  --internal-url URL     private URL used by master, usually http://NODE_IP:3020
  --registration-file P root-only file copied into the master form
  --non-interactive      fail instead of prompting for URLs
  -h, --help             show this help

After installation open Administration -> Nodes -> Create node on master, choose
"Hikvision node" and enter the exact values from the registration file.
USAGE
}

while (($#)); do
  case "$1" in
    --master-url) MASTER_URL="${2:-}"; shift 2 ;;
    --node-id) NODE_ID="${2:-}"; shift 2 ;;
    --node-token) NODE_TOKEN="${2:-}"; shift 2 ;;
    --media-secret) MEDIA_SECRET="${2:-}"; shift 2 ;;
    --public-url) PUBLIC_URL="${2:-}"; shift 2 ;;
    --internal-url) INTERNAL_URL="${2:-}"; shift 2 ;;
    --registration-file) REGISTRATION_FILE="${2:-}"; shift 2 ;;
    --non-interactive) NON_INTERACTIVE=true; shift ;;
    -h|--help) usage; exit 0 ;;
    *) fail "Unknown option: $1" ;;
  esac
done

[[ "$(id -u)" -eq 0 ]] || fail "Run as root"
for command in node npm ffmpeg ffprobe rsync systemctl openssl gzip python3 curl; do
  command -v "$command" >/dev/null || fail "$command is required"
done

existing_value() {
  local key="$1"
  [[ -r "$ENV_FILE" ]] || return 0
  sed -n "s/^${key}=//p" "$ENV_FILE" | tail -1
}

prompt_url() {
  local variable="$1" prompt="$2" default_value="${3:-}"
  [[ -n "${!variable}" ]] && return 0
  if [[ "$NON_INTERACTIVE" == true || ! -t 0 ]]; then
    [[ -n "$default_value" ]] || fail "$variable is required"
    printf -v "$variable" '%s' "$default_value"
    return 0
  fi
  local answer
  read -r -p "$prompt${default_value:+ [$default_value]}: " answer
  printf -v "$variable" '%s' "${answer:-$default_value}"
}

validate_url() {
  python3 - "$1" <<'PY'
import sys
from urllib.parse import urlparse
v=sys.argv[1].strip(); p=urlparse(v)
if p.scheme not in {'http','https'} or not p.hostname or p.username or p.password:
    raise SystemExit(1)
PY
}

validate_uuid() {
  python3 - "$1" <<'PY'
import sys, uuid
uuid.UUID(sys.argv[1])
PY
}

valid_secret() {
  [[ ${#1} -ge 16 && ${#1} -le 512 && "$1" =~ ^[A-Za-z0-9._~-]+$ ]]
}

[[ -n "$MASTER_URL" ]] || MASTER_URL="$(existing_value DVR_MASTER_URL)"
[[ -n "$NODE_ID" ]] || NODE_ID="$(existing_value DVR_NODE_ID)"
[[ -n "$NODE_TOKEN" ]] || NODE_TOKEN="$(existing_value DVR_NODE_TOKEN)"
[[ -n "$MEDIA_SECRET" ]] || MEDIA_SECRET="$(existing_value DVR_NODE_MEDIA_SECRET)"
[[ -n "$PUBLIC_URL" ]] || PUBLIC_URL="$(existing_value DVR_NODE_PUBLIC_BASE_URL)"
[[ -n "$INTERNAL_URL" ]] || INTERNAL_URL="$(existing_value DVR_NODE_INTERNAL_URL)"

PRIMARY_IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
DEFAULT_NODE_URL="${PRIMARY_IP:+http://${PRIMARY_IP}:3020}"
prompt_url MASTER_URL "DVR_MASTER_URL"
prompt_url PUBLIC_URL "DVR_NODE_PUBLIC_BASE_URL" "$DEFAULT_NODE_URL"
prompt_url INTERNAL_URL "DVR_NODE_INTERNAL_URL" "$DEFAULT_NODE_URL"

[[ -n "$NODE_ID" && "$NODE_ID" != REPLACE_* ]] || NODE_ID="$(cat /proc/sys/kernel/random/uuid)"
[[ -n "$NODE_TOKEN" && "$NODE_TOKEN" != REPLACE_* ]] || NODE_TOKEN="$(openssl rand -hex 32)"
[[ -n "$MEDIA_SECRET" && "$MEDIA_SECRET" != REPLACE_* ]] || MEDIA_SECRET="$(openssl rand -hex 32)"
STATE_KEY="$(existing_value HIK_NODE_STATE_KEY)"
[[ -n "$STATE_KEY" && "$STATE_KEY" != REPLACE_* ]] || STATE_KEY="$(openssl rand -hex 32)"

MASTER_URL="${MASTER_URL%/}"
PUBLIC_URL="${PUBLIC_URL%/}"
INTERNAL_URL="${INTERNAL_URL%/}"
validate_url "$MASTER_URL" || fail "Invalid DVR_MASTER_URL"
validate_url "$PUBLIC_URL" || fail "Invalid DVR_NODE_PUBLIC_BASE_URL"
validate_url "$INTERNAL_URL" || fail "Invalid DVR_NODE_INTERNAL_URL"
validate_uuid "$NODE_ID" || fail "DVR_NODE_ID must be a UUID"
valid_secret "$NODE_TOKEN" || fail "Invalid DVR_NODE_TOKEN"
valid_secret "$MEDIA_SECRET" || fail "Invalid DVR_NODE_MEDIA_SECRET"
[[ "$REGISTRATION_FILE" = /* ]] || fail "Registration file must use an absolute path"

if ! id "$RUNTIME_USER" >/dev/null 2>&1; then
  useradd --system --home-dir "$DATA_DIR" --shell /usr/sbin/nologin "$RUNTIME_USER"
fi
install -d -m 0750 -o "$RUNTIME_USER" -g "$RUNTIME_USER" "$DATA_DIR" "$DATA_DIR/live" "$DATA_DIR/archive" "$DATA_DIR/tmp"
install -d -m 0755 "$PROJECT_DIR"
install -d -m 0750 "$ENV_DIR"

log "Copying project"
rsync -a --delete --exclude '.git/' --exclude 'node_modules/' --exclude 'dist/' "$SOURCE_DIR/" "$PROJECT_DIR/"

sed \
  -e "s|REPLACE_WITH_MASTER_URL|$MASTER_URL|" \
  -e "s|REPLACE_WITH_NODE_ID|$NODE_ID|" \
  -e "s|REPLACE_WITH_NODE_TOKEN|$NODE_TOKEN|" \
  -e "s|REPLACE_WITH_MEDIA_SECRET|$MEDIA_SECRET|" \
  -e "s|REPLACE_WITH_PUBLIC_URL|$PUBLIC_URL|" \
  -e "s|REPLACE_WITH_INTERNAL_URL|$INTERNAL_URL|" \
  -e "s|REPLACE_WITH_32_BYTE_HEX_KEY|$STATE_KEY|" \
  "$PROJECT_DIR/deploy/env/app.env.example" >"$ENV_FILE"
chown root:"$RUNTIME_USER" "$ENV_FILE"
chmod 0640 "$ENV_FILE"

cat >"$REGISTRATION_FILE" <<REGISTRATION
# Enter these exact values in Administration -> Nodes -> Create node.
NODE_KIND=hikvision
DVR_MASTER_URL=$MASTER_URL
DVR_NODE_ID=$NODE_ID
DVR_NODE_TOKEN=$NODE_TOKEN
DVR_NODE_MEDIA_SECRET=$MEDIA_SECRET
DVR_NODE_PUBLIC_BASE_URL=$PUBLIC_URL
DVR_NODE_INTERNAL_URL=$INTERNAL_URL
REGISTRATION
chown root:root "$REGISTRATION_FILE"
chmod 0600 "$REGISTRATION_FILE"

log "Installing dependencies and building"
cd "$PROJECT_DIR"
gzip -dc package-lock.json.gz > package-lock.json
umask 022
npm ci --include=dev
npm run check
npm prune --omit=dev
chown -R root:root "$PROJECT_DIR"

install -m 0644 "$PROJECT_DIR/deploy/systemd/newdomofon-video-hik.service" "$SERVICE_FILE"
systemctl daemon-reload
systemctl enable newdomofon-video-hik.service
systemctl restart newdomofon-video-hik.service

log "Verifying local service"
for _ in $(seq 1 30); do
  if curl -fsS http://127.0.0.1:3020/health; then
    echo
    echo "Hikvision-node is installed locally."
    echo "Now create a matching Hikvision node on master using:"
    echo "  $REGISTRATION_FILE"
    echo "The service may log 401 until the matching master record exists."
    exit 0
  fi
  sleep 1
done
journalctl -u newdomofon-video-hik.service -n 100 --no-pager
fail "Health check failed"
