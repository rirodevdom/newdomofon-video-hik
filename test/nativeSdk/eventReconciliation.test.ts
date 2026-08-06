import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';

const configSource = fs.readFileSync('src/config.ts', 'utf8');
const runtimeSource = fs.readFileSync('src/nativeSdk/deviceRuntime.ts', 'utf8');
const collectorSource = fs.readFileSync('src/nativeSdk/eventCollector.ts', 'utf8');
const storeSource = fs.readFileSync('src/events/eventStore.ts', 'utf8');
const workerSource = fs.readFileSync('native-sdk/hik_sdk_device_worker.cpp', 'utf8');

test('historical event reconciliation is enabled with bounded defaults', () => {
  assert.match(configSource, /HIK_EVENT_SYNC_ENABLED', true/);
  assert.match(configSource, /HIK_EVENT_SYNC_SECONDS', 60, 15/);
  assert.match(configSource, /HIK_EVENT_SYNC_OVERLAP_SECONDS', 120, 30/);
  assert.match(configSource, /HIK_EVENT_SYNC_INITIAL_LOOKBACK_SECONDS', 3600, 60/);
});

test('grouped runtime accepts event scan commands without spawning another SDK process', () => {
  assert.match(runtimeSource, /requestGroupedEventScan/);
  assert.match(runtimeSource, /'EVENT_SCAN'/);
  assert.match(workerSource, /NET_DVR_FindFileByEvent_V40/);
  assert.match(workerSource, /NET_DVR_FindNextEvent_V40/);
  assert.match(workerSource, /NET_DVR_FindFile_V40/);
  assert.match(workerSource, /NET_DVR_FindNextFile_V40/);
  assert.match(workerSource, /fields\[0\] == "EVENT_SCAN"/);
});

test('event scan covers motion, VCA behavior and smart detection with recording-index fallback', () => {
  assert.match(workerSource, /scan_event_major\(sdk, liveSinks, requestId, 0/);
  assert.match(workerSource, /scan_event_major\(sdk, liveSinks, requestId, 2/);
  assert.match(workerSource, /scan_event_major\(sdk, liveSinks, requestId, 4/);
  assert.match(workerSource, /scan_recording_index/);
  assert.match(workerSource, /recording_event_name/);
});

test('historical events are persisted to the existing SQLite store with cross-source dedupe', () => {
  assert.match(collectorSource, /hikvision\.hcnetsdk\.history/);
  assert.match(collectorSource, /dedupe_window_ms: 5000/);
  assert.match(storeSource, /CREATE TABLE IF NOT EXISTS event_sync_state/);
  assert.match(storeSource, /event_sync_state/);
  assert.match(storeSource, /BETWEEN \? AND \?/);
});

test('realtime callback remains enabled alongside historical reconciliation', () => {
  assert.match(collectorSource, /grouped HCNetSDK alarm consumer enabled/);
  assert.match(collectorSource, /onNativeRuntimeAlarm/);
  assert.match(collectorSource, /historical HCNetSDK reconciliation enabled/);
});
