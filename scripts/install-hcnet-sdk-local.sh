#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

SOURCE="${1:-}"
SDK_ROOT="${HIK_SDK_ROOT:-/opt/hikvision/hcnetsdk}"
PROJECT_DIR="${PROJECT_DIR:-/opt/newdomofon-video-hik}"
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
    *.tar.gz|*.tgz)
      tar -xzf "$SOURCE" -C "$INPUT_ROOT"
      ;;
    *.tar.xz|*.txz)
      tar -xJf "$SOURCE" -C "$INPUT_ROOT"
      ;;
    *.tar)
      tar -xf "$SOURCE" -C "$INPUT_ROOT"
      ;;
    *)
      fail "Unsupported SDK archive. Pass an extracted directory, .zip, .tar.gz, .tar.xz or .tar"
      ;;
  esac
fi

HEADER="$(find "$INPUT_ROOT" -type f -name HCNetSDK.h -print -quit)"
LIB="$(find "$INPUT_ROOT" -type f \( -name libhcnetsdk.so -o -name libhcnetsdk.so.* \) -print -quit)"
[[ -n "$HEADER" ]] || fail "HCNetSDK.h was not found in the supplied package"
[[ -n "$LIB" ]] || fail "libhcnetsdk.so was not found in the supplied package"

HEADER_DIR="$(dirname "$HEADER")"
LIB_DIR="$(dirname "$LIB")"
RUNTIME_SOURCE="$LIB_DIR"

install -d -m 0755 "$SDK_ROOT/include" "$SDK_ROOT/runtime" "$SDK_ROOT/bin"
rsync -a --delete "$HEADER_DIR/" "$SDK_ROOT/include/"
rsync -a --delete "$RUNTIME_SOURCE/" "$SDK_ROOT/runtime/"

[[ -f "$SDK_ROOT/include/HCNetSDK.h" ]] || fail "Normalized HCNetSDK header is missing"
RUNTIME_LIB="$(find "$SDK_ROOT/runtime" -maxdepth 2 -type f \( -name libhcnetsdk.so -o -name libhcnetsdk.so.* \) -print -quit)"
[[ -n "$RUNTIME_LIB" ]] || fail "Normalized libhcnetsdk.so is missing"
RUNTIME_LIB_DIR="$(dirname "$RUNTIME_LIB")"

SOURCE_CPP="$PROJECT_DIR/native-sdk/hik_sdk_worker.cpp"
[[ -f "$SOURCE_CPP" ]] || fail "Worker source not found: $SOURCE_CPP"

log "Building native HCNetSDK worker"
g++ -std=c++17 -O2 -pthread \
  -I"$SDK_ROOT/include" \
  "$SOURCE_CPP" \
  -L"$RUNTIME_LIB_DIR" -lhcnetsdk \
  -Wl,-rpath,"$RUNTIME_LIB_DIR" \
  -o "$SDK_ROOT/bin/hik-sdk-worker.bin"
chmod 0755 "$SDK_ROOT/bin/hik-sdk-worker.bin"

cat >"$SDK_ROOT/bin/hik-sdk-worker" <<EOF
#!/usr/bin/env bash
set -euo pipefail
export HIK_SDK_LIB_DIR="${RUNTIME_LIB_DIR}"
export LD_LIBRARY_PATH="${RUNTIME_LIB_DIR}:\${LD_LIBRARY_PATH:-}"
exec "${SDK_ROOT}/bin/hik-sdk-worker.bin" "\$@"
EOF
chmod 0755 "$SDK_ROOT/bin/hik-sdk-worker"

cat >"$SDK_ROOT/sdk.env" <<EOF
HIK_SDK_ROOT=$SDK_ROOT
HIK_SDK_LIB_DIR=$RUNTIME_LIB_DIR
HIK_SDK_WORKER=$SDK_ROOT/bin/hik-sdk-worker
HIK_SDK_DEFAULT_PORT=8000
EOF
chmod 0644 "$SDK_ROOT/sdk.env"

log "HCNetSDK installed locally from operator-supplied package"
log "Worker: $SDK_ROOT/bin/hik-sdk-worker"
log "No Hikvision SDK binaries were downloaded by this script"
"$SDK_ROOT/bin/hik-sdk-worker" 2>&1 | head -n 1 || true
