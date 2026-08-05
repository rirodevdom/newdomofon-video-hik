#!/usr/bin/env bash
set -Eeuo pipefail

SDK_ROOT="${HIK_SDK_ROOT:-/opt/hikvision/hcnetsdk}"
SERVICE_USER="${HIK_SERVICE_USER:-newdomofon-hik}"
WORKER="$SDK_ROOT/bin/hik-sdk-worker"
CHANNEL_PROBE="$SDK_ROOT/bin/hik-sdk-channel-probe"
DEVICE_WORKER="$SDK_ROOT/bin/hik-sdk-device-worker"

fail() { echo "ERROR: $*" >&2; exit 1; }

[[ -x "$WORKER" ]] || fail "HCNetSDK worker is missing or not executable: $WORKER"
[[ -x "$CHANNEL_PROBE" ]] || fail "HCNetSDK channel probe is missing or not executable: $CHANNEL_PROBE"
[[ -x "$DEVICE_WORKER" ]] || fail "HCNetSDK grouped device worker is missing or not executable: $DEVICE_WORKER"
id "$SERVICE_USER" >/dev/null 2>&1 || fail "Service user does not exist: $SERVICE_USER"
command -v runuser >/dev/null 2>&1 || fail "runuser is required"

loader_check() {
  local label="$1"
  shift
  set +e
  local output
  output="$(runuser -u "$SERVICE_USER" -- "$@" 2>&1)"
  local rc=$?
  set -e
  if grep -Eqi 'error while loading shared libraries|permission denied' <<<"$output"; then
    fail "$SERVICE_USER cannot load HCNetSDK runtime through $label: $output"
  fi
  printf '%s\n%s\n' "$rc" "$output"
}

worker_result="$(loader_check "$WORKER" "$WORKER")"
worker_rc="$(head -n1 <<<"$worker_result")"
worker_output="$(tail -n +2 <<<"$worker_result")"
if ! grep -q 'usage: hik-sdk-worker' <<<"$worker_output"; then
  fail "Unexpected service-user worker probe (rc=$worker_rc): $worker_output"
fi

probe_result="$(loader_check "$CHANNEL_PROBE" "$CHANNEL_PROBE")"
probe_rc="$(head -n1 <<<"$probe_result")"
probe_output="$(tail -n +2 <<<"$probe_result")"
if ! grep -q 'missing required environment variable: HIK_SDK_HOST' <<<"$probe_output"; then
  fail "Unexpected service-user channel-probe result (rc=$probe_rc): $probe_output"
fi

device_result="$(loader_check "$DEVICE_WORKER" "$DEVICE_WORKER")"
device_rc="$(head -n1 <<<"$device_result")"
device_output="$(tail -n +2 <<<"$device_result")"
if ! grep -q 'missing required environment variable: HIK_SDK_HOST' <<<"$device_output"; then
  fail "Unexpected service-user grouped-device result (rc=$device_rc): $device_output"
fi

echo "HCNetSDK runtime is readable and loadable by service user: $SERVICE_USER"
