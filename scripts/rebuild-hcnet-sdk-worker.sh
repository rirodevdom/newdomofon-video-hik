#!/usr/bin/env bash
set -Eeuo pipefail

SDK_ROOT="${HIK_SDK_ROOT:-/opt/hikvision/hcnetsdk}"
PROJECT_DIR="${PROJECT_DIR:-/opt/newdomofon-video-hik}"
SERVICE_USER="${HIK_SERVICE_USER:-newdomofon-hik}"
HEADER="$SDK_ROOT/include/HCNetSDK.h"
WORKER_SOURCE="$PROJECT_DIR/native-sdk/hik_sdk_worker.cpp"
CHANNEL_SOURCE="$PROJECT_DIR/native-sdk/hik_sdk_channel_probe.cpp"
DEVICE_SOURCE="$PROJECT_DIR/native-sdk/hik_sdk_device_worker.cpp"

fail() { echo "ERROR: $*" >&2; exit 1; }
[[ -f "$HEADER" ]] || fail "HCNetSDK header is not installed: $HEADER"
[[ -f "$WORKER_SOURCE" ]] || fail "Native worker source is missing: $WORKER_SOURCE"
[[ -f "$CHANNEL_SOURCE" ]] || fail "Native channel probe source is missing: $CHANNEL_SOURCE"
[[ -f "$DEVICE_SOURCE" ]] || fail "Native grouped device worker source is missing: $DEVICE_SOURCE"
command -v g++ >/dev/null 2>&1 || fail "g++ is required to rebuild the installed HCNetSDK worker"

LIB="$(find "$SDK_ROOT/runtime" -maxdepth 3 \( -type f -o -type l \) \( -name libhcnetsdk.so -o -name 'libhcnetsdk.so.*' \) -print -quit)"
[[ -n "$LIB" ]] || fail "libhcnetsdk.so is not installed under $SDK_ROOT/runtime"
LIB_DIR="$(dirname "$LIB")"
install -d -m 0755 "$SDK_ROOT/bin"

chown -R root:root "$SDK_ROOT"
find "$SDK_ROOT" -type d -exec chmod 0755 {} +
find "$SDK_ROOT/include" "$SDK_ROOT/runtime" -type f -exec chmod a+r {} +
find "$SDK_ROOT/include" "$SDK_ROOT/runtime" -type f -exec chmod go-w {} +

python3 "$PROJECT_DIR/scripts/patch-archive-live-coexistence.py" --project-dir "$PROJECT_DIR"
python3 "$PROJECT_DIR/scripts/patch-smartyard-virtual-archive-segments.py" --project-dir "$PROJECT_DIR" --native-only

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
build_native "$DEVICE_SOURCE" "$SDK_ROOT/bin/hik-sdk-device-worker.bin"

write_wrapper() {
  local name="$1"
  cat >"$SDK_ROOT/bin/$name" <<EOF
#!/usr/bin/env bash
set -euo pipefail
export HIK_SDK_LIB_DIR="$LIB_DIR"
export LD_LIBRARY_PATH="$LIB_DIR:\${LD_LIBRARY_PATH:-}"
exec "$SDK_ROOT/bin/$name.bin" "\$@"
EOF
  chmod 0755 "$SDK_ROOT/bin/$name"
}

write_wrapper hik-sdk-worker
write_wrapper hik-sdk-channel-probe
write_wrapper hik-sdk-device-worker

HIK_SDK_ROOT="$SDK_ROOT" HIK_SERVICE_USER="$SERVICE_USER" \
  bash "$PROJECT_DIR/scripts/verify-hcnet-sdk-runtime.sh"

echo "HCNetSDK worker rebuilt: $SDK_ROOT/bin/hik-sdk-worker"
echo "HCNetSDK channel probe rebuilt: $SDK_ROOT/bin/hik-sdk-channel-probe"
echo "HCNetSDK grouped device worker rebuilt: $SDK_ROOT/bin/hik-sdk-device-worker"
