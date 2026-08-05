#!/usr/bin/env bash
set -Eeuo pipefail

SDK_ROOT="${HIK_SDK_ROOT:-/opt/hikvision/hcnetsdk}"
SERVICE_USER="${HIK_SERVICE_USER:-newdomofon-hik}"
WORKER="$SDK_ROOT/bin/hik-sdk-worker"
CHANNEL_PROBE="$SDK_ROOT/bin/hik-sdk-channel-probe"

fail() { echo "ERROR: $*" >&2; exit 1; }

[[ -x "$WORKER" ]] || fail "HCNetSDK worker is missing or not executable: $WORKER"
[[ -x "$CHANNEL_PROBE" ]] || fail "HCNetSDK channel probe is missing or not executable: $CHANNEL_PROBE"
id "$SERVICE_USER" >/dev/null 2>&1 || fail "Service user does not exist: $SERVICE_USER"
command -v runuser >/dev/null 2>&1 || fail "runuser is required"

# The main worker prints usage before touching device credentials. Running it
# as the actual service account proves that ld.so can load libhcnetsdk.so.
set +e
worker_output="$(runuser -u "$SERVICE_USER" -- "$WORKER" 2>&1)"
worker_rc=$?
set -e
if grep -Eqi 'error while loading shared libraries|permission denied' <<<"$worker_output"; then
  fail "$SERVICE_USER cannot load HCNetSDK runtime through $WORKER: $worker_output"
fi
if ! grep -q 'usage: hik-sdk-worker' <<<"$worker_output"; then
  fail "Unexpected service-user worker probe (rc=$worker_rc): $worker_output"
fi

# The channel helper has no usage mode; after successful library loading it
# reaches our own credential check. Missing HIK_SDK_HOST is expected here.
set +e
probe_output="$(runuser -u "$SERVICE_USER" -- "$CHANNEL_PROBE" 2>&1)"
probe_rc=$?
set -e
if grep -Eqi 'error while loading shared libraries|permission denied' <<<"$probe_output"; then
  fail "$SERVICE_USER cannot load HCNetSDK runtime through $CHANNEL_PROBE: $probe_output"
fi
if ! grep -q 'missing required environment variable: HIK_SDK_HOST' <<<"$probe_output"; then
  fail "Unexpected service-user channel-probe result (rc=$probe_rc): $probe_output"
fi

echo "HCNetSDK runtime is readable and loadable by service user: $SERVICE_USER"
