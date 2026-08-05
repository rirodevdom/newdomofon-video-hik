#!/usr/bin/env bash
set -Eeuo pipefail

SDK_ROOT="${HIK_SDK_ROOT:-/opt/hikvision/hcnetsdk}"
PROJECT_DIR="${PROJECT_DIR:-/opt/newdomofon-video-hik}"
HEADER="$SDK_ROOT/include/HCNetSDK.h"
WORKER_SOURCE="$PROJECT_DIR/native-sdk/hik_sdk_worker.cpp"
CHANNEL_SOURCE="$PROJECT_DIR/native-sdk/hik_sdk_channel_probe.cpp"

fail() { echo "ERROR: $*" >&2; exit 1; }
[[ -f "$HEADER" ]] || fail "HCNetSDK header is not installed: $HEADER"
[[ -f "$WORKER_SOURCE" ]] || fail "Native worker source is missing: $WORKER_SOURCE"
[[ -f "$CHANNEL_SOURCE" ]] || fail "Native channel probe source is missing: $CHANNEL_SOURCE"
command -v g++ >/dev/null 2>&1 || fail "g++ is required to rebuild the installed HCNetSDK worker"

LIB="$(find "$SDK_ROOT/runtime" -maxdepth 3 -type f \( -name libhcnetsdk.so -o -name 'libhcnetsdk.so.*' \) -print -quit)"
[[ -n "$LIB" ]] || fail "libhcnetsdk.so is not installed under $SDK_ROOT/runtime"
LIB_DIR="$(dirname "$LIB")"
install -d -m 0755 "$SDK_ROOT/bin"

build_native() {
  local source="$1"
  local output="$2"
  g++ -std=c++17 -O2 -pthread \
    -I"$SDK_ROOT/include" \
    "$source" \
    -L"$LIB_DIR" -lhcnetsdk \
    -Wl,-rpath,"$LIB_DIR" \
    -o "$output.new"
  chmod 0755 "$output.new"
  mv -f "$output.new" "$output"
}

echo "Rebuilding HCNetSDK workers from current project source"
build_native "$WORKER_SOURCE" "$SDK_ROOT/bin/hik-sdk-worker.bin"
build_native "$CHANNEL_SOURCE" "$SDK_ROOT/bin/hik-sdk-channel-probe.bin"

cat >"$SDK_ROOT/bin/hik-sdk-worker" <<EOF
#!/usr/bin/env bash
set -euo pipefail
export HIK_SDK_LIB_DIR="$LIB_DIR"
export LD_LIBRARY_PATH="$LIB_DIR:\${LD_LIBRARY_PATH:-}"
exec "$SDK_ROOT/bin/hik-sdk-worker.bin" "\$@"
EOF
chmod 0755 "$SDK_ROOT/bin/hik-sdk-worker"

cat >"$SDK_ROOT/bin/hik-sdk-channel-probe" <<EOF
#!/usr/bin/env bash
set -euo pipefail
export HIK_SDK_LIB_DIR="$LIB_DIR"
export LD_LIBRARY_PATH="$LIB_DIR:\${LD_LIBRARY_PATH:-}"
exec "$SDK_ROOT/bin/hik-sdk-channel-probe.bin" "\$@"
EOF
chmod 0755 "$SDK_ROOT/bin/hik-sdk-channel-probe"

"$SDK_ROOT/bin/hik-sdk-worker" 2>&1 | head -n 1 || true
echo "HCNetSDK worker rebuilt: $SDK_ROOT/bin/hik-sdk-worker"
echo "HCNetSDK channel probe rebuilt: $SDK_ROOT/bin/hik-sdk-channel-probe"
