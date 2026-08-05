#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

SOURCE="${1:-}"
SDK_ROOT="${HIK_SDK_ROOT:-/opt/hikvision/hcnetsdk}"
SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROJECT_DIR="${PROJECT_DIR:-$SOURCE_DIR}"
WORK_DIR=""

fail() { echo "ERROR: $*" >&2; exit 1; }
log() { printf '[%s] %s\n' "$(date '+%F %T')" "$*"; }
cleanup() { [[ -n "$WORK_DIR" && -d "$WORK_DIR" ]] && rm -rf "$WORK_DIR"; }
trap cleanup EXIT

[[ "$(id -u)" -eq 0 ]] || fail "Run as root"
[[ -n "$SOURCE" ]] || fail "Usage: sudo bash scripts/install-hcnet-sdk-local.sh /root/Device_Network_SDK_Linux64_PACKAGE"
[[ -e "$SOURCE" ]] || fail "SDK package/path not found: $SOURCE"
command -v g++ >/dev/null 2>&1 || fail "g++ is required (install build-essential first)"
command -v find >/dev/null 2>&1 || fail "find is required"
command -v rsync >/dev/null 2>&1 || fail "rsync is required"

WORK_DIR="$(mktemp -d /tmp/newdomofon-hcnet-sdk.XXXXXX)"
INPUT_ROOT="$SOURCE"
if [[ -f "$SOURCE" ]]; then
  INPUT_ROOT="$WORK_DIR/unpacked"
  mkdir -p "$INPUT_ROOT"
  case "$SOURCE" in
    *.zip)
      command -v unzip >/dev/null 2>&1 || fail "unzip is required for .zip package"
      unzip -q "$SOURCE" -d "$INPUT_ROOT"
      ;;
    *.tar.gz|*.tgz) tar -xzf "$SOURCE" -C "$INPUT_ROOT" ;;
    *.tar.xz|*.txz) tar -xJf "$SOURCE" -C "$INPUT_ROOT" ;;
    *.tar) tar -xf "$SOURCE" -C "$INPUT_ROOT" ;;
    *) fail "Unsupported SDK archive. Pass an extracted directory, .zip, .tar.gz, .tar.xz or .tar" ;;
  esac
fi

HEADER="$(find "$INPUT_ROOT" -type f -name HCNetSDK.h -print -quit)"
LIB="$(find "$INPUT_ROOT" -type f \( -name libhcnetsdk.so -o -name 'libhcnetsdk.so.*' \) -print -quit)"
[[ -n "$HEADER" ]] || fail "HCNetSDK.h was not found in the supplied package"
[[ -n "$LIB" ]] || fail "libhcnetsdk.so was not found in the supplied package"

HEADER_DIR="$(dirname "$HEADER")"
LIB_DIR="$(dirname "$LIB")"
install -d -m 0755 "$SDK_ROOT/include" "$SDK_ROOT/runtime" "$SDK_ROOT/bin"
rsync -a --delete "$HEADER_DIR/" "$SDK_ROOT/include/"
rsync -a --delete "$LIB_DIR/" "$SDK_ROOT/runtime/"

[[ -f "$SDK_ROOT/include/HCNetSDK.h" ]] || fail "Normalized HCNetSDK header is missing"
[[ -f "$PROJECT_DIR/scripts/rebuild-hcnet-sdk-worker.sh" ]] || fail "Worker rebuild script is missing"

log "Building native HCNetSDK worker and channel inventory helper"
PROJECT_DIR="$PROJECT_DIR" HIK_SDK_ROOT="$SDK_ROOT" bash "$PROJECT_DIR/scripts/rebuild-hcnet-sdk-worker.sh"

RUNTIME_LIB="$(find "$SDK_ROOT/runtime" -maxdepth 3 -type f \( -name libhcnetsdk.so -o -name 'libhcnetsdk.so.*' \) -print -quit)"
RUNTIME_LIB_DIR="$(dirname "$RUNTIME_LIB")"
cat >"$SDK_ROOT/sdk.env" <<EOF
HIK_SDK_ROOT=$SDK_ROOT
HIK_SDK_LIB_DIR=$RUNTIME_LIB_DIR
HIK_SDK_WORKER=$SDK_ROOT/bin/hik-sdk-worker
HIK_SDK_CHANNEL_PROBE=$SDK_ROOT/bin/hik-sdk-channel-probe
HIK_SDK_DEFAULT_PORT=8000
EOF
chmod 0644 "$SDK_ROOT/sdk.env"

log "HCNetSDK installed locally from operator-supplied package"
log "Worker: $SDK_ROOT/bin/hik-sdk-worker"
log "Channel probe: $SDK_ROOT/bin/hik-sdk-channel-probe"
log "No Hikvision SDK binaries were downloaded by this script"
