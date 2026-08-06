#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

MARKER = "newdomofon-hik-native-event-backfill"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if new in text:
        return text
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one source block, found {count}")
    return text.replace(old, new, 1)


def patch_config(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    old = """  eventSyncInitialLookbackSeconds: numberEnv('HIK_EVENT_SYNC_INITIAL_LOOKBACK_SECONDS', 3600, 60),

  requestTimeoutMs:"""
    new = """  eventSyncInitialLookbackSeconds: numberEnv('HIK_EVENT_SYNC_INITIAL_LOOKBACK_SECONDS', 3600, 60),
  eventBackfillEnabled: boolEnv('HIK_EVENT_BACKFILL_ENABLED', true),
  eventBackfillRetentionDays: numberEnv('HIK_EVENT_BACKFILL_RETENTION_DAYS', 30, 1),
  eventBackfillChunkSeconds: numberEnv('HIK_EVENT_BACKFILL_CHUNK_SECONDS', 21600, 300),
  eventBackfillIntervalSeconds: numberEnv('HIK_EVENT_BACKFILL_INTERVAL_SECONDS', 30, 10),

  requestTimeoutMs:"""
    path.write_text(replace_once(text, old, new, "native event backfill config"), encoding="utf-8")


def patch_event_store(path: Path) -> None:
    text = path.read_text(encoding="utf-8")

    old_schema = """      CREATE TABLE IF NOT EXISTS event_sync_state (
        device_id TEXT PRIMARY KEY,
        last_success_ms INTEGER,
        last_scan_started_ms INTEGER,
        last_scan_finished_ms INTEGER,
        last_error TEXT,
        events_found INTEGER NOT NULL DEFAULT 0,
        source TEXT
      );
    `);"""
    new_schema = """      CREATE TABLE IF NOT EXISTS event_sync_state (
        device_id TEXT PRIMARY KEY,
        last_success_ms INTEGER,
        last_scan_started_ms INTEGER,
        last_scan_finished_ms INTEGER,
        last_error TEXT,
        events_found INTEGER NOT NULL DEFAULT 0,
        source TEXT
      );
      CREATE TABLE IF NOT EXISTS event_backfill_state (
        device_id TEXT PRIMARY KEY,
        cursor_end_ms INTEGER NOT NULL,
        target_start_ms INTEGER NOT NULL,
        complete INTEGER NOT NULL DEFAULT 0,
        last_success_ms INTEGER,
        last_scan_started_ms INTEGER,
        last_scan_finished_ms INTEGER,
        last_error TEXT,
        events_found INTEGER NOT NULL DEFAULT 0,
        total_events_found INTEGER NOT NULL DEFAULT 0,
        oldest_event_ms INTEGER
      );
    `);"""
    text = replace_once(text, old_schema, new_schema, "event backfill state schema")

    health_anchor = """export function getHikvisionEventStoreHealth() {"""
    helpers = """export type HikvisionEventBackfillState = {
  device_id: string;
  cursor_at: string;
  target_start_at: string;
  complete: boolean;
  last_success_at: string | null;
  last_scan_started_at: string | null;
  last_scan_finished_at: string | null;
  last_error: string | null;
  events_found: number;
  total_events_found: number;
  oldest_event_at: string | null;
};

function backfillRow(row: Record<string, unknown>): HikvisionEventBackfillState {
  const iso = (value: unknown) => value == null ? null : new Date(Number(value)).toISOString();
  return {
    device_id: String(row.device_id),
    cursor_at: new Date(Number(row.cursor_end_ms)).toISOString(),
    target_start_at: new Date(Number(row.target_start_ms)).toISOString(),
    complete: Number(row.complete || 0) === 1,
    last_success_at: iso(row.last_success_ms),
    last_scan_started_at: iso(row.last_scan_started_ms),
    last_scan_finished_at: iso(row.last_scan_finished_ms),
    last_error: row.last_error == null ? null : String(row.last_error),
    events_found: Number(row.events_found || 0),
    total_events_found: Number(row.total_events_found || 0),
    oldest_event_at: iso(row.oldest_event_ms)
  };
}

export function getHikvisionEventBackfillState(deviceId: string): HikvisionEventBackfillState | null {
  const row = database().prepare(`
    SELECT device_id, cursor_end_ms, target_start_ms, complete,
           last_success_ms, last_scan_started_ms, last_scan_finished_ms,
           last_error, events_found, total_events_found, oldest_event_ms
      FROM event_backfill_state WHERE device_id = ?
  `).get(deviceId);
  return row ? backfillRow(row) : null;
}

export function initializeHikvisionEventBackfillState(input: {
  deviceId: string;
  cursorEndMs: number;
  targetStartMs: number;
}): HikvisionEventBackfillState {
  database().prepare(`
    INSERT OR IGNORE INTO event_backfill_state(
      device_id, cursor_end_ms, target_start_ms, complete,
      events_found, total_events_found
    ) VALUES (?, ?, ?, 0, 0, 0)
  `).run(input.deviceId, input.cursorEndMs, input.targetStartMs);
  const state = getHikvisionEventBackfillState(input.deviceId);
  if (!state) throw new Error(`Failed to initialize event backfill state for ${input.deviceId}`);
  return state;
}

export function updateHikvisionEventBackfillState(input: {
  deviceId: string;
  cursorEndMs?: number;
  targetStartMs?: number;
  complete?: boolean;
  lastSuccessMs?: number | null;
  scanStartedMs?: number | null;
  scanFinishedMs?: number | null;
  lastError?: string | null;
  eventsFound?: number;
  addEventsFound?: number;
  oldestEventMs?: number | null;
}): void {
  const previous = getHikvisionEventBackfillState(input.deviceId);
  if (!previous) throw new Error(`Event backfill state is not initialized for ${input.deviceId}`);
  const parse = (value: string | null) => value ? Date.parse(value) : null;
  const previousOldest = parse(previous.oldest_event_at);
  const nextOldest = input.oldestEventMs == null
    ? previousOldest
    : previousOldest == null ? input.oldestEventMs : Math.min(previousOldest, input.oldestEventMs);
  database().prepare(`
    UPDATE event_backfill_state
       SET cursor_end_ms = ?,
           target_start_ms = ?,
           complete = ?,
           last_success_ms = ?,
           last_scan_started_ms = ?,
           last_scan_finished_ms = ?,
           last_error = ?,
           events_found = ?,
           total_events_found = ?,
           oldest_event_ms = ?
     WHERE device_id = ?
  `).run(
    input.cursorEndMs ?? Date.parse(previous.cursor_at),
    input.targetStartMs ?? Date.parse(previous.target_start_at),
    input.complete === undefined ? (previous.complete ? 1 : 0) : (input.complete ? 1 : 0),
    input.lastSuccessMs !== undefined ? input.lastSuccessMs : parse(previous.last_success_at),
    input.scanStartedMs !== undefined ? input.scanStartedMs : parse(previous.last_scan_started_at),
    input.scanFinishedMs !== undefined ? input.scanFinishedMs : parse(previous.last_scan_finished_at),
    input.lastError !== undefined ? input.lastError : previous.last_error,
    input.eventsFound !== undefined ? input.eventsFound : previous.events_found,
    previous.total_events_found + Math.max(0, Math.trunc(input.addEventsFound || 0)),
    nextOldest,
    input.deviceId
  );
}

export function listHikvisionEventBackfillStates(): HikvisionEventBackfillState[] {
  return database().prepare(`
    SELECT device_id, cursor_end_ms, target_start_ms, complete,
           last_success_ms, last_scan_started_ms, last_scan_finished_ms,
           last_error, events_found, total_events_found, oldest_event_ms
      FROM event_backfill_state ORDER BY device_id
  `).all().map(backfillRow);
}

export function getHikvisionEventStoreHealth() {"""
    text = replace_once(text, health_anchor, helpers, "event backfill state helpers")

    old_health = """      sync: {
        enabled: config.eventSyncEnabled,
        interval_seconds: config.eventSyncIntervalSeconds,
        devices: listHikvisionEventSyncStates()
      }
    };"""
    new_health = """      sync: {
        enabled: config.eventSyncEnabled,
        interval_seconds: config.eventSyncIntervalSeconds,
        devices: listHikvisionEventSyncStates(),
        backfill: {
          enabled: config.eventBackfillEnabled,
          retention_days: config.eventBackfillRetentionDays,
          chunk_seconds: config.eventBackfillChunkSeconds,
          interval_seconds: config.eventBackfillIntervalSeconds,
          devices: listHikvisionEventBackfillStates()
        }
      }
    };"""
    text = replace_once(text, old_health, new_health, "event backfill health")
    path.write_text(text, encoding="utf-8")


def patch_event_collector(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    if MARKER in text:
        return

    old_import = """  appendHikvisionEvent,
  cleanupHikvisionEvents,
  getHikvisionEventSyncState,
  updateHikvisionEventSyncState
} from '../events/eventStore.js';"""
    new_import = """  appendHikvisionEvent,
  cleanupHikvisionEvents,
  getHikvisionEventSyncState,
  updateHikvisionEventSyncState,
  getHikvisionEventBackfillState,
  initializeHikvisionEventBackfillState,
  updateHikvisionEventBackfillState
} from '../events/eventStore.js';"""
    text = replace_once(text, old_import, new_import, "event backfill store imports")

    text = replace_once(
        text,
        """const NATIVE_EVENT_RECONCILIATION = 'newdomofon-hik-native-event-reconciliation';""",
        """const NATIVE_EVENT_RECONCILIATION = 'newdomofon-hik-native-event-reconciliation';
const NATIVE_EVENT_BACKFILL = 'newdomofon-hik-native-event-backfill';""",
        "event backfill marker",
    )

    old_pending = """type PendingScan = {
  requestId: string;
  deviceId: string;
  startMs: number;
  endMs: number;
  startedAt: number;
  timeout: NodeJS.Timeout;
};"""
    new_pending = """type PendingScan = {
  requestId: string;
  deviceId: string;
  startMs: number;
  endMs: number;
  startedAt: number;
  timeout: NodeJS.Timeout;
  purpose: 'realtime' | 'backfill';
  targetStartMs: number | null;
  oldestEventMs: number | null;
};"""
    text = replace_once(text, old_pending, new_pending, "event scan purpose state")

    text = replace_once(
        text,
        """  private syncTimer: NodeJS.Timeout | null = null;
  private initialSyncTimer: NodeJS.Timeout | null = null;""",
        """  private syncTimer: NodeJS.Timeout | null = null;
  private initialSyncTimer: NodeJS.Timeout | null = null;
  private backfillTimer: NodeJS.Timeout | null = null;
  private initialBackfillTimer: NodeJS.Timeout | null = null;""",
        "event backfill timers",
    )

    old_start = """    if (config.eventSyncEnabled) {
      console.log(`[hikvision-events] historical HCNetSDK reconciliation enabled interval=${config.eventSyncIntervalSeconds}s overlap=${config.eventSyncOverlapSeconds}s`);
      this.initialSyncTimer = setTimeout(() => { void this.reconcileHistory(); }, 5_000);
      this.initialSyncTimer.unref?.();
      this.syncTimer = setInterval(() => { void this.reconcileHistory(); }, config.eventSyncIntervalSeconds * 1000);
      this.syncTimer.unref?.();
    }
  }"""
    new_start = """    if (config.eventSyncEnabled) {
      console.log(`[hikvision-events] historical HCNetSDK reconciliation enabled interval=${config.eventSyncIntervalSeconds}s overlap=${config.eventSyncOverlapSeconds}s`);
      this.initialSyncTimer = setTimeout(() => { void this.reconcileHistory(); }, 5_000);
      this.initialSyncTimer.unref?.();
      this.syncTimer = setInterval(() => { void this.reconcileHistory(); }, config.eventSyncIntervalSeconds * 1000);
      this.syncTimer.unref?.();
    }
    if (config.eventBackfillEnabled) {
      console.log(`[hikvision-events] historical backfill enabled retention=${config.eventBackfillRetentionDays}d chunk=${config.eventBackfillChunkSeconds}s interval=${config.eventBackfillIntervalSeconds}s`);
      this.initialBackfillTimer = setTimeout(() => { void this.reconcileBackfill(); }, 12_000);
      this.initialBackfillTimer.unref?.();
      this.backfillTimer = setInterval(() => { void this.reconcileBackfill(); }, config.eventBackfillIntervalSeconds * 1000);
      this.backfillTimer.unref?.();
    }
  }"""
    text = replace_once(text, old_start, new_start, "start event backfill scheduler")

    old_stop = """    if (this.syncTimer) clearInterval(this.syncTimer);
    if (this.initialSyncTimer) clearTimeout(this.initialSyncTimer);
    this.cleanupTimer = null;
    this.syncTimer = null;
    this.initialSyncTimer = null;"""
    new_stop = """    if (this.syncTimer) clearInterval(this.syncTimer);
    if (this.initialSyncTimer) clearTimeout(this.initialSyncTimer);
    if (this.backfillTimer) clearInterval(this.backfillTimer);
    if (this.initialBackfillTimer) clearTimeout(this.initialBackfillTimer);
    this.cleanupTimer = null;
    this.syncTimer = null;
    this.initialSyncTimer = null;
    this.backfillTimer = null;
    this.initialBackfillTimer = null;"""
    text = replace_once(text, old_stop, new_stop, "stop event backfill scheduler")

    old_occurred = """    const occurredAt = String(alarm.occurred_at || new Date(pending.startMs).toISOString());
    const endedAt = String(alarm.ended_at || occurredAt);"""
    new_occurred = """    const occurredAt = String(alarm.occurred_at || new Date(pending.startMs).toISOString());
    const occurredMs = Date.parse(occurredAt);
    if (pending.purpose === 'backfill' && Number.isFinite(occurredMs)) {
      pending.oldestEventMs = pending.oldestEventMs == null ? occurredMs : Math.min(pending.oldestEventMs, occurredMs);
    }
    const endedAt = String(alarm.ended_at || occurredAt);"""
    text = replace_once(text, old_occurred, new_occurred, "track oldest backfilled event")

    old_status = """  private consumeScanStatus(deviceId: string, alarm: NativeRuntimeAlarm): void {
    const requestId = String(alarm.request_id || '');
    const pending = this.pendingByDevice.get(deviceId);
    if (!pending || pending.requestId !== requestId) return;
    clearTimeout(pending.timeout);
    this.pendingByDevice.delete(deviceId);
    const now = Date.now();
    const ok = alarm.ok === true;
    updateHikvisionEventSyncState({
      deviceId,
      lastSuccessMs: ok ? pending.endMs : undefined,
      scanFinishedMs: now,
      lastError: ok ? null : `HCNetSDK historical event scan failed${alarm.error_code ? ` error=${alarm.error_code}` : ''}`,
      eventsFound: Number(alarm.found || 0),
      source: String(alarm.source_kind || 'event_search')
    });
    if (!ok) {
      console.warn(`[hikvision-events:${deviceId}] historical reconciliation failed error=${alarm.error_code || 0}`);
    }
  }"""
    new_status = """  private consumeScanStatus(deviceId: string, alarm: NativeRuntimeAlarm): void {
    const requestId = String(alarm.request_id || '');
    const pending = this.pendingByDevice.get(deviceId);
    if (!pending || pending.requestId !== requestId) return;
    clearTimeout(pending.timeout);
    this.pendingByDevice.delete(deviceId);
    const now = Date.now();
    const ok = alarm.ok === true;
    const eventsFound = Number(alarm.found || 0);
    const errorText = `HCNetSDK historical event scan failed${alarm.error_code ? ` error=${alarm.error_code}` : ''}`;

    if (pending.purpose === 'backfill') {
      const state = getHikvisionEventBackfillState(deviceId);
      if (!state) return;
      const targetStartMs = pending.targetStartMs ?? Date.parse(state.target_start_at);
      const complete = ok && pending.startMs <= targetStartMs;
      updateHikvisionEventBackfillState({
        deviceId,
        cursorEndMs: ok ? pending.startMs : undefined,
        complete: ok ? complete : undefined,
        lastSuccessMs: ok ? now : undefined,
        scanFinishedMs: now,
        lastError: ok ? null : errorText,
        eventsFound,
        addEventsFound: ok ? eventsFound : 0,
        oldestEventMs: ok ? pending.oldestEventMs : undefined
      });
      if (ok) {
        console.log(`[hikvision-events:${deviceId}] backfill chunk ${new Date(pending.startMs).toISOString()}..${new Date(pending.endMs).toISOString()} found=${eventsFound}${complete ? ' complete=true' : ''}`);
      } else {
        console.warn(`[hikvision-events:${deviceId}] historical backfill failed error=${alarm.error_code || 0}`);
      }
      return;
    }

    updateHikvisionEventSyncState({
      deviceId,
      lastSuccessMs: ok ? pending.endMs : undefined,
      scanFinishedMs: now,
      lastError: ok ? null : errorText,
      eventsFound,
      source: String(alarm.source_kind || 'event_search')
    });
    if (!ok) {
      console.warn(`[hikvision-events:${deviceId}] historical reconciliation failed error=${alarm.error_code || 0}`);
    }
  }"""
    text = replace_once(text, old_status, new_status, "separate realtime and backfill scan status")

    old_pending_set = """      this.pendingByDevice.set(deviceId, { requestId, deviceId, startMs, endMs, startedAt, timeout });"""
    new_pending_set = """      this.pendingByDevice.set(deviceId, {
        requestId, deviceId, startMs, endMs, startedAt, timeout,
        purpose: 'realtime', targetStartMs: null, oldestEventMs: null
      });"""
    text = replace_once(text, old_pending_set, new_pending_set, "tag realtime event scan")

    class_tail = """  private async reconcileHistory(): Promise<void> {
    const now = Date.now();
    const endMs = now - 5_000;
    const maxLookbackMs = config.eventSyncInitialLookbackSeconds * 1000;
    const overlapMs = config.eventSyncOverlapSeconds * 1000;
    for (const snapshot of this.service.listDevices(false)) {
      const deviceId = snapshot.config.id;
      if (!snapshot.config.enabled || this.pendingByDevice.has(deviceId)) continue;
      const state = getHikvisionEventSyncState(deviceId);
      const lastSuccessMs = state?.last_success_at ? Date.parse(state.last_success_at) : 0;
      const startMs = Math.max(
        endMs - maxLookbackMs,
        lastSuccessMs > 0 ? lastSuccessMs - overlapMs : endMs - maxLookbackMs
      );
      if (endMs <= startMs) continue;
      const requestId = crypto.randomUUID().replace(/-/g, '');
      const startedAt = Date.now();
      updateHikvisionEventSyncState({ deviceId, scanStartedMs: startedAt, lastError: null });
      const timeout = setTimeout(() => {
        const pending = this.pendingByDevice.get(deviceId);
        if (!pending || pending.requestId !== requestId) return;
        this.pendingByDevice.delete(deviceId);
        updateHikvisionEventSyncState({
          deviceId,
          scanFinishedMs: Date.now(),
          lastError: 'HCNetSDK historical event scan timed out',
          eventsFound: 0
        });
      }, Math.max(30_000, config.nativeSdkCommandTimeoutMs * 2));
      timeout.unref?.();
      this.pendingByDevice.set(deviceId, {
        requestId, deviceId, startMs, endMs, startedAt, timeout,
        purpose: 'realtime', targetStartMs: null, oldestEventMs: null
      });
      try {
        await requestGroupedEventScan({
          deviceId,
          requestId,
          start: new Date(startMs),
          end: new Date(endMs)
        });
      } catch (error) {
        clearTimeout(timeout);
        this.pendingByDevice.delete(deviceId);
        updateHikvisionEventSyncState({
          deviceId,
          scanFinishedMs: Date.now(),
          lastError: error instanceof Error ? error.message : String(error),
          eventsFound: 0
        });
      }
    }
  }
}"""
    replacement_tail = """  private async reconcileHistory(): Promise<void> {
    const now = Date.now();
    const endMs = now - 5_000;
    const maxLookbackMs = config.eventSyncInitialLookbackSeconds * 1000;
    const overlapMs = config.eventSyncOverlapSeconds * 1000;
    for (const snapshot of this.service.listDevices(false)) {
      const deviceId = snapshot.config.id;
      if (!snapshot.config.enabled || this.pendingByDevice.has(deviceId)) continue;
      const state = getHikvisionEventSyncState(deviceId);
      const lastSuccessMs = state?.last_success_at ? Date.parse(state.last_success_at) : 0;
      const startMs = Math.max(
        endMs - maxLookbackMs,
        lastSuccessMs > 0 ? lastSuccessMs - overlapMs : endMs - maxLookbackMs
      );
      if (endMs <= startMs) continue;
      const requestId = crypto.randomUUID().replace(/-/g, '');
      const startedAt = Date.now();
      updateHikvisionEventSyncState({ deviceId, scanStartedMs: startedAt, lastError: null });
      const timeout = setTimeout(() => {
        const pending = this.pendingByDevice.get(deviceId);
        if (!pending || pending.requestId !== requestId) return;
        this.pendingByDevice.delete(deviceId);
        updateHikvisionEventSyncState({
          deviceId,
          scanFinishedMs: Date.now(),
          lastError: 'HCNetSDK historical event scan timed out',
          eventsFound: 0
        });
      }, Math.max(30_000, config.nativeSdkCommandTimeoutMs * 2));
      timeout.unref?.();
      this.pendingByDevice.set(deviceId, {
        requestId, deviceId, startMs, endMs, startedAt, timeout,
        purpose: 'realtime', targetStartMs: null, oldestEventMs: null
      });
      try {
        await requestGroupedEventScan({
          deviceId,
          requestId,
          start: new Date(startMs),
          end: new Date(endMs)
        });
      } catch (error) {
        clearTimeout(timeout);
        this.pendingByDevice.delete(deviceId);
        updateHikvisionEventSyncState({
          deviceId,
          scanFinishedMs: Date.now(),
          lastError: error instanceof Error ? error.message : String(error),
          eventsFound: 0
        });
      }
    }
  }

  private async reconcileBackfill(): Promise<void> {
    const now = Date.now();
    const initialCursorMs = now - config.eventSyncInitialLookbackSeconds * 1000;
    const defaultTargetMs = now - config.eventBackfillRetentionDays * 24 * 3600_000;
    const chunkMs = config.eventBackfillChunkSeconds * 1000;

    for (const snapshot of this.service.listDevices(false)) {
      const deviceId = snapshot.config.id;
      if (!snapshot.config.enabled || this.pendingByDevice.has(deviceId)) continue;

      let state = getHikvisionEventBackfillState(deviceId);
      if (!state) {
        state = initializeHikvisionEventBackfillState({
          deviceId,
          cursorEndMs: initialCursorMs,
          targetStartMs: defaultTargetMs
        });
      }
      if (state.complete) continue;

      const cursorEndMs = Date.parse(state.cursor_at);
      const targetStartMs = Date.parse(state.target_start_at);
      if (!Number.isFinite(cursorEndMs) || !Number.isFinite(targetStartMs)) continue;
      if (cursorEndMs <= targetStartMs) {
        updateHikvisionEventBackfillState({ deviceId, complete: true, lastError: null });
        continue;
      }

      const endMs = cursorEndMs;
      const startMs = Math.max(targetStartMs, endMs - chunkMs);
      const requestId = crypto.randomUUID().replace(/-/g, '');
      const startedAt = Date.now();
      updateHikvisionEventBackfillState({
        deviceId,
        scanStartedMs: startedAt,
        lastError: null
      });
      const timeout = setTimeout(() => {
        const pending = this.pendingByDevice.get(deviceId);
        if (!pending || pending.requestId !== requestId) return;
        this.pendingByDevice.delete(deviceId);
        updateHikvisionEventBackfillState({
          deviceId,
          scanFinishedMs: Date.now(),
          lastError: 'HCNetSDK historical backfill scan timed out',
          eventsFound: 0
        });
      }, Math.max(120_000, config.nativeSdkCommandTimeoutMs * 4));
      timeout.unref?.();
      this.pendingByDevice.set(deviceId, {
        requestId, deviceId, startMs, endMs, startedAt, timeout,
        purpose: 'backfill', targetStartMs, oldestEventMs: null
      });
      try {
        await requestGroupedEventScan({
          deviceId,
          requestId,
          start: new Date(startMs),
          end: new Date(endMs)
        });
      } catch (error) {
        clearTimeout(timeout);
        this.pendingByDevice.delete(deviceId);
        updateHikvisionEventBackfillState({
          deviceId,
          scanFinishedMs: Date.now(),
          lastError: error instanceof Error ? error.message : String(error),
          eventsFound: 0
        });
      }
    }
  }
}"""
    text = replace_once(text, class_tail, replacement_tail, "persistent historical event backfill")
    path.write_text(text, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--project-dir', default='.')
    args = parser.parse_args()
    root = Path(args.project_dir).resolve()
    patch_config(root / 'src/config.ts')
    patch_event_store(root / 'src/events/eventStore.ts')
    patch_event_collector(root / 'src/nativeSdk/eventCollector.ts')
    print('Native historical event backfill prepared: persistent 30-day cursor with six-hour chunks')
    print('Realtime event reconciliation remains active while backfill walks older DVR history')


if __name__ == '__main__':
    main()
