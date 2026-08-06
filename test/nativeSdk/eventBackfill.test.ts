import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';

const configSource = fs.readFileSync('src/config.ts', 'utf8');
const storeSource = fs.readFileSync('src/events/eventStore.ts', 'utf8');
const collectorSource = fs.readFileSync('src/nativeSdk/eventCollector.ts', 'utf8');
const updaterSource = fs.readFileSync('scripts/update-installed-project.sh', 'utf8');

test('event backfill defaults to persistent 30-day history in six-hour chunks', () => {
  assert.match(configSource, /HIK_EVENT_BACKFILL_ENABLED', true/);
  assert.match(configSource, /HIK_EVENT_BACKFILL_RETENTION_DAYS', 30/);
  assert.match(configSource, /HIK_EVENT_BACKFILL_CHUNK_SECONDS', 21600/);
  assert.match(configSource, /HIK_EVENT_BACKFILL_INTERVAL_SECONDS', 30/);
});

test('backfill cursor is stored separately from realtime sync cursor', () => {
  assert.match(storeSource, /CREATE TABLE IF NOT EXISTS event_backfill_state/);
  assert.match(storeSource, /cursor_end_ms INTEGER NOT NULL/);
  assert.match(storeSource, /target_start_ms INTEGER NOT NULL/);
  assert.match(storeSource, /total_events_found INTEGER NOT NULL DEFAULT 0/);
  assert.match(storeSource, /oldest_event_ms INTEGER/);
  assert.match(storeSource, /initializeHikvisionEventBackfillState/);
  assert.match(storeSource, /updateHikvisionEventBackfillState/);
});

test('realtime reconciliation stays active while backfill walks backwards', () => {
  assert.match(collectorSource, /purpose: 'realtime'/);
  assert.match(collectorSource, /purpose: 'backfill'/);
  assert.match(collectorSource, /reconcileHistory\(\)/);
  assert.match(collectorSource, /reconcileBackfill\(\)/);
  assert.match(collectorSource, /now - config\.eventSyncInitialLookbackSeconds \* 1000/);
  assert.match(collectorSource, /now - config\.eventBackfillRetentionDays \* 24 \* 3600_000/);
  assert.match(collectorSource, /config\.eventBackfillChunkSeconds \* 1000/);
  assert.match(collectorSource, /cursorEndMs: ok \? pending\.startMs/);
});

test('backfill health exposes progress and oldest recovered event', () => {
  assert.match(storeSource, /backfill: \{/);
  assert.match(storeSource, /retention_days: config\.eventBackfillRetentionDays/);
  assert.match(storeSource, /devices: listHikvisionEventBackfillStates\(\)/);
  assert.match(storeSource, /oldest_event_at/);
  assert.match(storeSource, /complete: Number\(row\.complete/);
});

test('production updater enables backfill without changing archive limits', () => {
  assert.match(updaterSource, /HIK_EVENT_BACKFILL_ENABLED true/);
  assert.match(updaterSource, /HIK_EVENT_BACKFILL_RETENTION_DAYS 30/);
  assert.match(updaterSource, /HIK_EVENT_BACKFILL_CHUNK_SECONDS 21600/);
  assert.match(updaterSource, /HIK_EVENT_BACKFILL_INTERVAL_SECONDS 30/);
  assert.match(updaterSource, /HIK_DEVICE_ARCHIVE_MAX_ACTIVE_PER_DVR 4/);
});
