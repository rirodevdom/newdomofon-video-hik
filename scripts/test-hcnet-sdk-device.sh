#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

SDK_ROOT="${HIK_SDK_ROOT:-/opt/hikvision/hcnetsdk}"
WORKER="${HIK_SDK_WORKER:-$SDK_ROOT/bin/hik-sdk-worker}"
HOST="${HIK_SDK_HOST:-}"
PORT="${HIK_SDK_PORT:-8000}"
USERNAME="${HIK_SDK_USERNAME:-}"
PASSWORD="${HIK_SDK_PASSWORD:-}"
CHANNEL="${HIK_SDK_CHANNEL:-1}"
START="${HIK_SDK_START:-}"
END="${HIK_SDK_END:-}"
OUT_DIR="${HIK_SDK_TEST_OUT:-/tmp/newdomofon-hcnet-sdk-test}"

fail() { echo "ERROR: $*" >&2; exit 1; }

[[ -x "$WORKER" ]] || fail "HCNetSDK worker not installed: $WORKER"
[[ -n "$HOST" ]] || fail "Set HIK_SDK_HOST"
[[ -n "$USERNAME" ]] || fail "Set HIK_SDK_USERNAME"
[[ -n "$PASSWORD" ]] || fail "Set HIK_SDK_PASSWORD"

export HIK_SDK_HOST="$HOST" HIK_SDK_PORT="$PORT" HIK_SDK_USERNAME="$USERNAME" HIK_SDK_PASSWORD="$PASSWORD"
mkdir -p "$OUT_DIR"
chmod 0700 "$OUT_DIR"

echo "=== 1. Private SDK login / channel probe ==="
"$WORKER" probe | tee "$OUT_DIR/probe.json"

if [[ -n "$START" && -n "$END" ]]; then
  echo
  echo "=== 2. Native archive search ==="
  export HIK_SDK_CHANNEL="$CHANNEL" HIK_SDK_START="$START" HIK_SDK_END="$END"
  "$WORKER" ranges | tee "$OUT_DIR/ranges.json"

  echo
  echo "=== 3. Native playback callback, 8 seconds ==="
  set +e
  timeout 8s "$WORKER" playback >"$OUT_DIR/playback.ps"
  PLAYBACK_RC=$?
  set -e
  [[ "$PLAYBACK_RC" -eq 0 || "$PLAYBACK_RC" -eq 124 || "$PLAYBACK_RC" -eq 143 ]] || fail "Playback worker failed with rc=$PLAYBACK_RC"
  stat -c 'playback bytes=%s' "$OUT_DIR/playback.ps"
else
  echo
  echo "Archive test skipped. Set HIK_SDK_START and HIK_SDK_END as UTC ISO timestamps."
fi

echo
echo "=== 4. Native live callback, 8 seconds ==="
export HIK_SDK_CHANNEL="$CHANNEL"
set +e
timeout 8s "$WORKER" live >"$OUT_DIR/live.ps"
LIVE_RC=$?
set -e
[[ "$LIVE_RC" -eq 0 || "$LIVE_RC" -eq 124 || "$LIVE_RC" -eq 143 ]] || fail "Live worker failed with rc=$LIVE_RC"
stat -c 'live bytes=%s' "$OUT_DIR/live.ps"

echo
echo "=== 5. Native alarm channel, 15 seconds ==="
set +e
timeout 15s "$WORKER" events | tee "$OUT_DIR/events.jsonl"
EVENT_RC=${PIPESTATUS[0]}
set -e
[[ "$EVENT_RC" -eq 0 || "$EVENT_RC" -eq 124 || "$EVENT_RC" -eq 143 ]] || fail "Event worker failed with rc=$EVENT_RC"

echo
echo "HCNetSDK native transport test complete: $OUT_DIR"
echo "No RTSP URL or ISAPI HTTP endpoint is used by this test."
