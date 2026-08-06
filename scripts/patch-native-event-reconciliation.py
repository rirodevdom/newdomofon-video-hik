#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

MARKER = "newdomofon-hik-native-event-reconciliation"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if new in text:
        return text
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one source block, found {count}")
    return text.replace(old, new, 1)


def patch_config(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    old = """  nativeSdkFallback: boolEnv('HIK_NATIVE_SDK_FALLBACK', false),

  requestTimeoutMs:"""
    new = """  nativeSdkFallback: boolEnv('HIK_NATIVE_SDK_FALLBACK', false),
  eventSyncEnabled: boolEnv('HIK_EVENT_SYNC_ENABLED', true),
  eventSyncIntervalSeconds: numberEnv('HIK_EVENT_SYNC_SECONDS', 60, 15),
  eventSyncOverlapSeconds: numberEnv('HIK_EVENT_SYNC_OVERLAP_SECONDS', 120, 30),
  eventSyncInitialLookbackSeconds: numberEnv('HIK_EVENT_SYNC_INITIAL_LOOKBACK_SECONDS', 3600, 60),

  requestTimeoutMs:"""
    path.write_text(replace_once(text, old, new, "native event sync config"), encoding="utf-8")


def patch_runtime_events(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    old = """export interface NativeRuntimeAlarm {
  command?: number;
  event_type?: string;
  event_state?: string;
  physical_channel?: number;
  alarm_type?: number;
  event_code?: number;
  occurred_at?: string;
}"""
    new = """export interface NativeRuntimeAlarm {
  kind?: 'alarm' | 'historical_event' | 'event_scan_status';
  request_id?: string;
  command?: number;
  event_type?: string;
  event_state?: string;
  physical_channel?: number;
  alarm_type?: number;
  event_code?: number;
  event_major?: number;
  event_minor?: number;
  file_type?: number;
  source_kind?: string;
  occurred_at?: string;
  ended_at?: string;
  ok?: boolean;
  found?: number;
  error_code?: number;
}"""
    path.write_text(replace_once(text, old, new, "native historical event payload"), encoding="utf-8")


def patch_device_runtime(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    anchor = """export function stopGroupedPlayback(deviceId: string, sessionId: string): Promise<void> {
  try {
    return writeCommandWithAck(deviceId, ['STOP_PLAYBACK', sessionId], sessionId, 'stop');
  } catch {
    return Promise.resolve();
  }
}"""
    replacement = anchor + """

export function requestGroupedEventScan(input: {
  deviceId: string;
  requestId: string;
  start: Date;
  end: Date;
}): Promise<void> {
  let current: RegisteredRuntime;
  try {
    current = runtime(input.deviceId);
  } catch (error) {
    return Promise.reject(error);
  }
  const fields = [
    'EVENT_SCAN',
    safeField(input.requestId, 'event scan request id'),
    safeField(input.start.toISOString(), 'event scan start'),
    safeField(input.end.toISOString(), 'event scan end')
  ];
  const line = `${fields.join('\\t')}\\n`;
  return new Promise((resolve, reject) => {
    current.child.stdin!.write(line, (error) => {
      if (error) reject(Object.assign(error, { statusCode: 503 }));
      else resolve();
    });
  });
}"""
    path.write_text(replace_once(text, anchor, replacement, "grouped event scan command"), encoding="utf-8")


def patch_event_store(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    text = replace_once(
        text,
        """  data?: Record<string, unknown> | null;
};""",
        """  data?: Record<string, unknown> | null;
  dedupe_window_ms?: number;
};""",
        "event dedupe window input",
    )

    old_schema = """      CREATE INDEX IF NOT EXISTS camera_events_type_time
        ON camera_events(event_type, occurred_at_ms);
    `);"""
    new_schema = """      CREATE INDEX IF NOT EXISTS camera_events_type_time
        ON camera_events(event_type, occurred_at_ms);
      CREATE TABLE IF NOT EXISTS event_sync_state (
        device_id TEXT PRIMARY KEY,
        last_success_ms INTEGER,
        last_scan_started_ms INTEGER,
        last_scan_finished_ms INTEGER,
        last_error TEXT,
        events_found INTEGER NOT NULL DEFAULT 0,
        source TEXT
      );
    `);"""
    text = replace_once(text, old_schema, new_schema, "event sync state schema")

    old_insert = """  const occurredAtMs = normalizeOccurredAt(input.occurred_at);
  const createdAtMs = Date.now();
  const eventHash = hashEvent(input, occurredAtMs, data);
  const id = input.id || crypto.randomUUID();"""
    new_insert = """  const occurredAtMs = normalizeOccurredAt(input.occurred_at);
  const createdAtMs = Date.now();
  const dedupeWindowMs = Math.max(0, Math.min(30_000, Math.trunc(Number(input.dedupe_window_ms || 0))));
  if (dedupeWindowMs > 0) {
    const existing = store.prepare(`
      SELECT id FROM camera_events
       WHERE channel_id = ? AND event_type = ?
         AND occurred_at_ms BETWEEN ? AND ?
       ORDER BY ABS(occurred_at_ms - ?) ASC LIMIT 1
    `).get(input.channel_id, input.event_type, occurredAtMs - dedupeWindowMs, occurredAtMs + dedupeWindowMs, occurredAtMs);
    if (existing?.id) return { inserted: false, id: String(existing.id) };
  }
  const eventHash = hashEvent(input, occurredAtMs, data);
  const id = input.id || crypto.randomUUID();"""
    text = replace_once(text, old_insert, new_insert, "cross-source event dedupe")

    health_anchor = """export function getHikvisionEventStoreHealth() {"""
    helpers = """export type HikvisionEventSyncState = {
  device_id: string;
  last_success_at: string | null;
  last_scan_started_at: string | null;
  last_scan_finished_at: string | null;
  last_error: string | null;
  events_found: number;
  source: string | null;
};

function syncRow(row: Record<string, unknown>): HikvisionEventSyncState {
  const iso = (value: unknown) => value == null ? null : new Date(Number(value)).toISOString();
  return {
    device_id: String(row.device_id),
    last_success_at: iso(row.last_success_ms),
    last_scan_started_at: iso(row.last_scan_started_ms),
    last_scan_finished_at: iso(row.last_scan_finished_ms),
    last_error: row.last_error == null ? null : String(row.last_error),
    events_found: Number(row.events_found || 0),
    source: row.source == null ? null : String(row.source)
  };
}

export function getHikvisionEventSyncState(deviceId: string): HikvisionEventSyncState | null {
  const row = database().prepare(`
    SELECT device_id, last_success_ms, last_scan_started_ms, last_scan_finished_ms,
           last_error, events_found, source
      FROM event_sync_state WHERE device_id = ?
  `).get(deviceId);
  return row ? syncRow(row) : null;
}

export function updateHikvisionEventSyncState(input: {
  deviceId: string;
  lastSuccessMs?: number | null;
  scanStartedMs?: number | null;
  scanFinishedMs?: number | null;
  lastError?: string | null;
  eventsFound?: number;
  source?: string | null;
}): void {
  const previous = getHikvisionEventSyncState(input.deviceId);
  const toMs = (value: string | null | undefined) => value ? Date.parse(value) : null;
  database().prepare(`
    INSERT INTO event_sync_state(
      device_id, last_success_ms, last_scan_started_ms, last_scan_finished_ms,
      last_error, events_found, source
    ) VALUES (?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(device_id) DO UPDATE SET
      last_success_ms = excluded.last_success_ms,
      last_scan_started_ms = excluded.last_scan_started_ms,
      last_scan_finished_ms = excluded.last_scan_finished_ms,
      last_error = excluded.last_error,
      events_found = excluded.events_found,
      source = excluded.source
  `).run(
    input.deviceId,
    input.lastSuccessMs !== undefined ? input.lastSuccessMs : toMs(previous?.last_success_at),
    input.scanStartedMs !== undefined ? input.scanStartedMs : toMs(previous?.last_scan_started_at),
    input.scanFinishedMs !== undefined ? input.scanFinishedMs : toMs(previous?.last_scan_finished_at),
    input.lastError !== undefined ? input.lastError : previous?.last_error ?? null,
    input.eventsFound !== undefined ? input.eventsFound : previous?.events_found ?? 0,
    input.source !== undefined ? input.source : previous?.source ?? null
  );
}

export function listHikvisionEventSyncStates(): HikvisionEventSyncState[] {
  return database().prepare(`
    SELECT device_id, last_success_ms, last_scan_started_ms, last_scan_finished_ms,
           last_error, events_found, source
      FROM event_sync_state ORDER BY device_id
  `).all().map(syncRow);
}

export function getHikvisionEventStoreHealth() {"""
    text = replace_once(text, health_anchor, helpers, "event sync state helpers")

    old_health_tail = """      last_error: lastError,
      retention_days: intEnv('HIK_EVENT_RETENTION_DAYS', 30, 1, 3650)
    };"""
    new_health_tail = """      last_error: lastError,
      retention_days: intEnv('HIK_EVENT_RETENTION_DAYS', 30, 1, 3650),
      sync: {
        enabled: config.eventSyncEnabled,
        interval_seconds: config.eventSyncIntervalSeconds,
        devices: listHikvisionEventSyncStates()
      }
    };"""
    text = replace_once(text, old_health_tail, new_health_tail, "event sync health")
    path.write_text(text, encoding="utf-8")


def patch_event_collector(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    if MARKER in text:
        return
    replacement = '''import crypto from 'node:crypto';
import { config } from '../config.js';
import type { HikvisionNodeService } from '../service.js';
import {
  appendHikvisionEvent,
  cleanupHikvisionEvents,
  getHikvisionEventSyncState,
  updateHikvisionEventSyncState
} from '../events/eventStore.js';
import { requestGroupedEventScan } from './deviceRuntime.js';
import { onNativeRuntimeAlarm, type NativeRuntimeAlarm } from './runtimeEvents.js';

const NATIVE_EVENT_RECONCILIATION = 'newdomofon-hik-native-event-reconciliation';

function enabled(): boolean {
  const raw = String(process.env.HIK_EVENTS_ENABLED ?? 'true').trim().toLowerCase();
  return ['1', 'true', 'yes', 'on'].includes(raw);
}

function stableHash(parts: Array<string | number | null | undefined>): string {
  return crypto.createHash('sha256').update(parts.map((value) => String(value ?? '')).join('|')).digest('hex');
}

type PendingScan = {
  requestId: string;
  deviceId: string;
  startMs: number;
  endMs: number;
  startedAt: number;
  timeout: NodeJS.Timeout;
};

export class NativeSdkEventCollector {
  private readonly lastEventAt = new Map<string, number>();
  private readonly pendingByDevice = new Map<string, PendingScan>();
  private cleanupTimer: NodeJS.Timeout | null = null;
  private syncTimer: NodeJS.Timeout | null = null;
  private initialSyncTimer: NodeJS.Timeout | null = null;
  private unsubscribe: (() => void) | null = null;

  constructor(private readonly service: HikvisionNodeService) {}

  start(): void {
    if (!enabled()) {
      console.log('[hikvision-events] native HCNetSDK collector disabled');
      return;
    }
    console.log('[hikvision-events] grouped HCNetSDK alarm consumer enabled');
    this.unsubscribe = onNativeRuntimeAlarm((deviceId, alarm) => this.consumeRuntimeEvent(deviceId, alarm));
    cleanupHikvisionEvents();
    this.cleanupTimer = setInterval(() => {
      try { cleanupHikvisionEvents(); }
      catch (error) { console.warn('[hikvision-events] retention failed', error instanceof Error ? error.message : error); }
    }, 60 * 60 * 1000);
    this.cleanupTimer.unref?.();

    if (config.eventSyncEnabled) {
      console.log(`[hikvision-events] historical HCNetSDK reconciliation enabled interval=${config.eventSyncIntervalSeconds}s overlap=${config.eventSyncOverlapSeconds}s`);
      this.initialSyncTimer = setTimeout(() => { void this.reconcileHistory(); }, 5_000);
      this.initialSyncTimer.unref?.();
      this.syncTimer = setInterval(() => { void this.reconcileHistory(); }, config.eventSyncIntervalSeconds * 1000);
      this.syncTimer.unref?.();
    }
  }

  stop(): void {
    if (this.cleanupTimer) clearInterval(this.cleanupTimer);
    if (this.syncTimer) clearInterval(this.syncTimer);
    if (this.initialSyncTimer) clearTimeout(this.initialSyncTimer);
    this.cleanupTimer = null;
    this.syncTimer = null;
    this.initialSyncTimer = null;
    for (const pending of this.pendingByDevice.values()) clearTimeout(pending.timeout);
    this.pendingByDevice.clear();
    this.unsubscribe?.();
    this.unsubscribe = null;
  }

  private consumeRuntimeEvent(deviceId: string, alarm: NativeRuntimeAlarm): void {
    if (alarm.kind === 'event_scan_status') {
      this.consumeScanStatus(deviceId, alarm);
      return;
    }
    if (alarm.kind === 'historical_event') {
      this.consumeHistoricalEvent(deviceId, alarm);
      return;
    }
    this.consumeAlarm(deviceId, alarm);
  }

  private channelFor(deviceId: string, rawChannel: number) {
    const snapshot = this.service.listDevices(false).find((item) => item.config.id === deviceId);
    if (!snapshot) return null;
    const channel = snapshot.channels.find((item) => item.physical_channel === rawChannel || item.sdk_channel === rawChannel);
    return channel ? { snapshot, channel } : null;
  }

  private consumeAlarm(deviceId: string, alarm: NativeRuntimeAlarm): void {
    const physical = Number(alarm.physical_channel || 0);
    if (!Number.isInteger(physical) || physical <= 0) return;
    const found = this.channelFor(deviceId, physical);
    if (!found) {
      console.warn(`[hikvision-events:${deviceId}] HCNetSDK alarm channel not mapped: ${physical}`);
      return;
    }
    const { snapshot, channel } = found;
    const eventType = String(alarm.event_type || 'hikvision_alarm');
    const occurredAt = String(alarm.occurred_at || new Date().toISOString());
    const eventMs = Date.parse(occurredAt) || Date.now();
    const dedupeKey = `${channel.id}|${eventType}|${alarm.event_code || 0}`;
    const previous = this.lastEventAt.get(dedupeKey) || 0;
    if (eventMs - previous < 2000) return;
    this.lastEventAt.set(dedupeKey, eventMs);
    appendHikvisionEvent({
      channel_id: channel.id,
      event_type: eventType,
      event_state: alarm.event_state || 'active',
      topic: eventType,
      source_name: 'hikvision.hcnetsdk',
      occurred_at: occurredAt,
      dedupe_window_ms: 5000,
      data: {
        device_id: snapshot.config.id,
        device_name: snapshot.config.name,
        physical_channel: channel.physical_channel,
        sdk_channel: channel.sdk_channel ?? channel.physical_channel,
        channel_name: channel.name,
        command: alarm.command ?? null,
        alarm_type: alarm.alarm_type ?? null,
        event_code: alarm.event_code ?? null,
        realtime: true
      }
    });
  }

  private consumeHistoricalEvent(deviceId: string, alarm: NativeRuntimeAlarm): void {
    const requestId = String(alarm.request_id || '');
    const pending = this.pendingByDevice.get(deviceId);
    if (!pending || pending.requestId !== requestId) return;
    const rawChannel = Number(alarm.physical_channel || 0);
    if (!Number.isInteger(rawChannel) || rawChannel <= 0) return;
    const found = this.channelFor(deviceId, rawChannel);
    if (!found) return;
    const { snapshot, channel } = found;
    const eventType = String(alarm.event_type || 'hikvision_event');
    const occurredAt = String(alarm.occurred_at || new Date(pending.startMs).toISOString());
    const endedAt = String(alarm.ended_at || occurredAt);
    const eventHash = stableHash([
      deviceId, channel.id, eventType, alarm.event_major, alarm.event_minor,
      alarm.file_type, occurredAt, endedAt
    ]);
    appendHikvisionEvent({
      event_hash: eventHash,
      channel_id: channel.id,
      event_type: eventType,
      event_state: 'active',
      topic: eventType,
      source_name: 'hikvision.hcnetsdk.history',
      occurred_at: occurredAt,
      dedupe_window_ms: 5000,
      data: {
        device_id: snapshot.config.id,
        device_name: snapshot.config.name,
        physical_channel: channel.physical_channel,
        sdk_channel: channel.sdk_channel ?? channel.physical_channel,
        channel_name: channel.name,
        event_major: alarm.event_major ?? null,
        event_minor: alarm.event_minor ?? null,
        file_type: alarm.file_type ?? null,
        ended_at: endedAt,
        source_kind: alarm.source_kind || 'event_search',
        historical_reconciliation: true
      }
    });
  }

  private consumeScanStatus(deviceId: string, alarm: NativeRuntimeAlarm): void {
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
  }

  private async reconcileHistory(): Promise<void> {
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
      this.pendingByDevice.set(deviceId, { requestId, deviceId, startMs, endMs, startedAt, timeout });
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
}
'''
    path.write_text(replacement, encoding="utf-8")


def patch_cpp_worker(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    if "EVENT_SCAN" in text and "scan_historical_events" in text:
        return
    text = replace_once(text, "#include <memory>\n", "#include <memory>\n#include <mutex>\n", "worker stdout mutex include")
    text = replace_once(
        text,
        """namespace {
std::atomic<bool> g_stop{false};""",
        """namespace {
std::atomic<bool> g_stop{false};
std::mutex g_stdout_mutex;""",
        "worker stdout mutex",
    )
    old_alarm = """void emit_alarm_json(LONG command, const char* eventType, int physicalChannel, DWORD alarmType, DWORD eventCode = 0) {
  std::cout
      << "{\\\"command\\\":" << command"""
    new_alarm = """void emit_alarm_json(LONG command, const char* eventType, int physicalChannel, DWORD alarmType, DWORD eventCode = 0) {
  std::lock_guard<std::mutex> lock(g_stdout_mutex);
  std::cout
      << "{\\\"kind\\\":\\\"alarm\\\",\\\"command\\\":" << command"""
    text = replace_once(text, old_alarm, new_alarm, "tag realtime alarm JSON")

    helpers_anchor = """std::vector<std::string> split_tabs(const std::string& line) {"""
    helpers = r'''std::string sdk_time_iso(const NET_DVR_TIME& value) {
  std::ostringstream out;
  out << std::setfill('0')
      << std::setw(4) << value.dwYear << '-'
      << std::setw(2) << value.dwMonth << '-'
      << std::setw(2) << value.dwDay << 'T'
      << std::setw(2) << value.dwHour << ':'
      << std::setw(2) << value.dwMinute << ':'
      << std::setw(2) << value.dwSecond << 'Z';
  return out.str();
}

const char* historical_event_name(WORD major, WORD minor) {
  if (major == 0) return "motion";
  if (major == 1) return "alarm_in";
  if (major == 2) {
    switch (minor) {
      case 0: return "line_crossing";
      case 1: return "enter_region";
      case 2: return "exit_region";
      case 3: return "intrusion";
      case 4: return "loitering";
      case 6: return "parking";
      case 7: return "rapid_move";
      case 8: return "crowd_density";
      case 12: return "face_detection";
      case 13: return "unattended_baggage";
      case 14: return "object_removed";
      case 23: return "audio_abnormal";
      case 28: return "safety_helmet";
      default: return "vca_behavior";
    }
  }
  if (major == 4) {
    switch (minor) {
      case 1: return "line_crossing";
      case 2: return "intrusion";
      case 3: return "audio_loss";
      case 4: return "audio_abnormal";
      case 5: return "face_detection";
      case 6: return "defocus";
      case 7: return "scene_change";
      case 8: return "pir";
      case 9: return "enter_region";
      case 10: return "exit_region";
      case 11: return "loitering";
      case 12: return "crowd_density";
      case 13: return "rapid_move";
      case 14: return "parking";
      case 15: return "unattended_baggage";
      case 16: return "object_removed";
      case 17: return "vehicle_detection";
      default: return "smart_detection";
    }
  }
  return "hikvision_event";
}

const char* recording_event_name(BYTE fileType) {
  switch (fileType) {
    case 1: return "motion";
    case 2: return "alarm";
    case 3: return "alarm_or_motion";
    case 4: return "alarm_and_motion";
    case 7: return "vibration_alarm";
    case 8: return "environment_alarm";
    case 9: return "smart_alarm";
    case 10: return "pir";
    case 11: return "wireless_alarm";
    case 12: return "call_help";
    case 13: return "alarm_family";
    case 14: return "traffic_event";
    case 15: return "line_crossing";
    case 16: return "intrusion";
    case 17: return "audio_abnormal";
    case 18: return "scene_change";
    case 19: return "smart_detection";
    case 20: return "face_detection";
    case 24: return "tamper";
    case 26: return "enter_region";
    case 27: return "exit_region";
    case 28: return "loitering";
    case 29: return "crowd_density";
    case 30: return "rapid_move";
    case 31: return "parking";
    case 32: return "unattended_baggage";
    case 33: return "object_removed";
    default: return nullptr;
  }
}

void emit_historical_event_json(const std::string& requestId,
                                const char* eventType,
                                int sdkChannel,
                                WORD major,
                                WORD minor,
                                int fileType,
                                const NET_DVR_TIME& start,
                                const NET_DVR_TIME& end,
                                const char* sourceKind) {
  std::lock_guard<std::mutex> lock(g_stdout_mutex);
  std::cout << "{\"kind\":\"historical_event\""
            << ",\"request_id\":\"" << requestId << "\""
            << ",\"event_type\":\"" << eventType << "\""
            << ",\"event_state\":\"active\""
            << ",\"physical_channel\":" << sdkChannel
            << ",\"event_major\":" << major
            << ",\"event_minor\":" << minor
            << ",\"file_type\":" << fileType
            << ",\"source_kind\":\"" << sourceKind << "\""
            << ",\"occurred_at\":\"" << sdk_time_iso(start) << "\""
            << ",\"ended_at\":\"" << sdk_time_iso(end) << "\"}"
            << std::endl;
}

void emit_event_scan_status(const std::string& requestId, bool ok, int found,
                            DWORD errorCode, const char* sourceKind) {
  std::lock_guard<std::mutex> lock(g_stdout_mutex);
  std::cout << "{\"kind\":\"event_scan_status\""
            << ",\"request_id\":\"" << requestId << "\""
            << ",\"ok\":" << (ok ? "true" : "false")
            << ",\"found\":" << found
            << ",\"error_code\":" << errorCode
            << ",\"source_kind\":\"" << sourceKind << "\"}"
            << std::endl;
}

void fill_channel_list(WORD* target, std::size_t count,
                       const std::vector<std::unique_ptr<LiveSink>>& liveSinks) {
  for (std::size_t index = 0; index < count; ++index) target[index] = 0xffff;
  std::size_t cursor = 0;
  for (const auto& sink : liveSinks) {
    if (!sink || sink->sdkChannel <= 0 || cursor >= count) continue;
    target[cursor++] = static_cast<WORD>(sink->sdkChannel);
  }
}

int scan_event_major(SdkDevice& sdk,
                     const std::vector<std::unique_ptr<LiveSink>>& liveSinks,
                     const std::string& requestId,
                     WORD major,
                     const NET_DVR_TIME& start,
                     const NET_DVR_TIME& end,
                     DWORD& lastError,
                     bool& supported) {
  NET_DVR_SEARCH_EVENT_PARAM_V40 param{};
  param.wMajorType = major;
  param.wMinorType = 0xffff;
  param.struStartTime = start;
  param.struEndTime = end;
  param.byLockType = 0xff;
  param.byQuickSearch = 1;
  if (major == 0) {
    fill_channel_list(param.uSeniorParam.struMotionParam.wMotDetChanNo,
                      sizeof(param.uSeniorParam.struMotionParam.wMotDetChanNo) / sizeof(WORD), liveSinks);
  } else if (major == 2) {
    fill_channel_list(param.uSeniorParam.struVcaParam.wChanNo,
                      sizeof(param.uSeniorParam.struVcaParam.wChanNo) / sizeof(WORD), liveSinks);
    param.uSeniorParam.struVcaParam.byRuleID = 0xff;
  } else if (major == 4) {
    param.uSeniorParam.struVCADetect.byAll = 0;
    fill_channel_list(param.uSeniorParam.struVCADetect.wChanNo,
                      sizeof(param.uSeniorParam.struVCADetect.wChanNo) / sizeof(WORD), liveSinks);
  }

  const LONG handle = NET_DVR_FindFileByEvent_V40(sdk.user_id(), &param);
  if (handle < 0) {
    lastError = NET_DVR_GetLastError();
    if (lastError != 23) supported = true;
    return 0;
  }
  supported = true;
  int found = 0;
  int findingLoops = 0;
  for (;;) {
    NET_DVR_SEARCH_EVENT_RET_V40 result{};
    const LONG status = NET_DVR_FindNextEvent_V40(handle, &result);
    if (status == NET_DVR_FILE_SUCCESS) {
      findingLoops = 0;
      bool emitted = false;
      for (std::size_t index = 0; index < sizeof(result.wChan) / sizeof(result.wChan[0]); ++index) {
        const WORD channel = result.wChan[index];
        if (channel == 0xffff) break;
        if (channel == 0) continue;
        emit_historical_event_json(requestId, historical_event_name(result.wMajorType, result.wMinorType),
                                   static_cast<int>(channel), result.wMajorType, result.wMinorType, -1,
                                   result.struStartTime, result.struEndTime, "event_search");
        ++found;
        emitted = true;
      }
      if (!emitted) {
        DWORD channel = 0;
        if (result.wMajorType == 0) channel = result.uSeniorRet.struMotionRet.dwMotDetNo;
        else if (result.wMajorType == 2) channel = result.uSeniorRet.struVcaRet.dwChanNo;
        if (channel > 0) {
          emit_historical_event_json(requestId, historical_event_name(result.wMajorType, result.wMinorType),
                                     static_cast<int>(channel), result.wMajorType, result.wMinorType, -1,
                                     result.struStartTime, result.struEndTime, "event_search");
          ++found;
        }
      }
      if (found >= 2000) break;
      continue;
    }
    if (status == NET_DVR_ISFINDING && findingLoops++ < 200) {
      std::this_thread::sleep_for(std::chrono::milliseconds(10));
      continue;
    }
    if (status == NET_DVR_FILE_NOFIND || status == NET_DVR_NOMOREFILE) break;
    lastError = NET_DVR_GetLastError();
    break;
  }
  NET_DVR_FindClose_V30(handle);
  return found;
}

int scan_recording_index(SdkDevice& sdk,
                         const std::vector<std::unique_ptr<LiveSink>>& liveSinks,
                         const std::string& requestId,
                         const NET_DVR_TIME& start,
                         const NET_DVR_TIME& end,
                         DWORD& lastError,
                         bool& supported) {
  int found = 0;
  for (const auto& sink : liveSinks) {
    if (!sink || sink->sdkChannel <= 0) continue;
    NET_DVR_FILECOND_V40 cond{};
    cond.lChannel = sink->sdkChannel;
    cond.dwFileType = 0xff;
    cond.dwIsLocked = 0xff;
    cond.struStartTime = start;
    cond.struStopTime = end;
    cond.byQuickSearch = 1;
    cond.byStreamType = 0xff;
    const LONG handle = NET_DVR_FindFile_V40(sdk.user_id(), &cond);
    if (handle < 0) {
      lastError = NET_DVR_GetLastError();
      if (lastError != 23) supported = true;
      continue;
    }
    supported = true;
    int findingLoops = 0;
    for (;;) {
      NET_DVR_FINDDATA_V40 result{};
      const LONG status = NET_DVR_FindNextFile_V40(handle, &result);
      if (status == NET_DVR_FILE_SUCCESS) {
        findingLoops = 0;
        const char* eventType = recording_event_name(result.byFileType);
        if (eventType) {
          emit_historical_event_json(requestId, eventType, sink->sdkChannel, 100, result.byFileType,
                                     result.byFileType, result.struStartTime, result.struStopTime,
                                     "recording_index");
          ++found;
        }
        if (found >= 2000) break;
        continue;
      }
      if (status == NET_DVR_ISFINDING && findingLoops++ < 200) {
        std::this_thread::sleep_for(std::chrono::milliseconds(10));
        continue;
      }
      if (status == NET_DVR_FILE_NOFIND || status == NET_DVR_NOMOREFILE) break;
      lastError = NET_DVR_GetLastError();
      break;
    }
    NET_DVR_FindClose_V30(handle);
    if (found >= 2000) break;
  }
  return found;
}

void scan_historical_events(SdkDevice& sdk,
                            const std::vector<std::unique_ptr<LiveSink>>& liveSinks,
                            const std::string& requestId,
                            const std::string& startRaw,
                            const std::string& endRaw) {
  NET_DVR_TIME start{};
  NET_DVR_TIME end{};
  if (!parse_iso_utc(startRaw, start) || !parse_iso_utc(endRaw, end)) {
    emit_event_scan_status(requestId, false, 0, 17, "invalid_time");
    return;
  }
  DWORD lastError = 0;
  bool eventSearchSupported = false;
  int found = 0;
  found += scan_event_major(sdk, liveSinks, requestId, 0, start, end, lastError, eventSearchSupported);
  found += scan_event_major(sdk, liveSinks, requestId, 2, start, end, lastError, eventSearchSupported);
  found += scan_event_major(sdk, liveSinks, requestId, 4, start, end, lastError, eventSearchSupported);

  const char* sourceKind = "event_search";
  bool fallbackSupported = false;
  if (found == 0) {
    const int fallbackFound = scan_recording_index(sdk, liveSinks, requestId, start, end, lastError, fallbackSupported);
    if (fallbackFound > 0 || fallbackSupported) sourceKind = "recording_index";
    found += fallbackFound;
  }
  const bool ok = eventSearchSupported || fallbackSupported;
  emit_event_scan_status(requestId, ok, found, ok ? 0 : lastError, sourceKind);
}

std::vector<std::string> split_tabs(const std::string& line) {'''
    text = replace_once(text, helpers_anchor, helpers, "historical event search helpers")

    command_anchor = """    if (fields[0] == "STOP_PLAYBACK" && fields.size() == 2) {
      stop_playback(sdk, playbacks, fields[1], true, true);
      continue;
    }

    std::cerr << "unknown grouped command: " << line << "\\n";"""
    command_new = """    if (fields[0] == "STOP_PLAYBACK" && fields.size() == 2) {
      stop_playback(sdk, playbacks, fields[1], true, true);
      continue;
    }

    if (fields[0] == "EVENT_SCAN" && fields.size() == 4) {
      scan_historical_events(sdk, sinks, fields[1], fields[2], fields[3]);
      continue;
    }

    std::cerr << "unknown grouped command: " << line << "\\n";"""
    text = replace_once(text, command_anchor, command_new, "grouped event scan command handler")
    path.write_text(text, encoding="utf-8")


def patch_stub(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    if "NET_DVR_SEARCH_EVENT_PARAM_V40" in text:
        return
    text = replace_once(
        text,
        """constexpr int MAX_CHANNUM_V30 = 64;
constexpr DWORD NET_DVR_SYSHEAD = 1;""",
        """constexpr int MAX_CHANNUM_V30 = 64;
constexpr int MAX_CHANNUM_V40 = 64;
constexpr DWORD NET_DVR_SYSHEAD = 1;
constexpr LONG NET_DVR_FILE_SUCCESS = 1000;
constexpr LONG NET_DVR_FILE_NOFIND = 1001;
constexpr LONG NET_DVR_ISFINDING = 1002;
constexpr LONG NET_DVR_NOMOREFILE = 1003;""",
        "stub event search status constants",
    )
    struct_anchor = """struct NET_DVR_SETUPALARM_PARAM {
  DWORD dwSize{};
  BYTE byLevel{};
  BYTE byAlarmInfoType{};
  BYTE byRetAlarmTypeV40{};
};"""
    structs = struct_anchor + r'''

struct NET_DVR_SEARCH_EVENT_PARAM_V40 {
  WORD wMajorType{};
  WORD wMinorType{};
  NET_DVR_TIME struStartTime{};
  NET_DVR_TIME struEndTime{};
  BYTE byLockType{};
  BYTE byQuickSearch{};
  BYTE byRes[130]{};
  union SeniorParam {
    struct { WORD wAlarmInNo[128]; BYTE byRes[544]; } struAlarmParam;
    struct { WORD wMotDetChanNo[MAX_CHANNUM_V30]; BYTE byRes[672]; } struMotionParam;
    struct { WORD wChanNo[MAX_CHANNUM_V30]; BYTE byRuleID; BYTE byRes[671]; } struVcaParam;
    struct { BYTE byAll; BYTE byRes1[3]; WORD wChanNo[MAX_CHANNUM_V30]; BYTE byRes2[668]; } struVCADetect;
    BYTE byLen[800];
    SeniorParam() : byLen{} {}
  } uSeniorParam;
};

struct NET_DVR_SEARCH_EVENT_RET_V40 {
  WORD wMajorType{};
  WORD wMinorType{};
  NET_DVR_TIME struStartTime{};
  NET_DVR_TIME struEndTime{};
  WORD wChan[MAX_CHANNUM_V40]{};
  BYTE byRes[36]{};
  union SeniorRet {
    struct { DWORD dwAlarmInNo; BYTE byRes[796]; } struAlarmRet;
    struct { DWORD dwMotDetNo; BYTE byRes[796]; } struMotionRet;
    struct { DWORD dwChanNo; BYTE byRuleID; BYTE byRes1[3]; BYTE byRuleName[32]; BYTE byRes[760]; } struVcaRet;
    BYTE byLen[800];
    SeniorRet() : byLen{} {}
  } uSeniorRet;
};

struct NET_DVR_FILECOND_V40 {
  LONG lChannel{};
  DWORD dwFileType{};
  DWORD dwIsLocked{};
  DWORD dwUseCardNo{};
  BYTE sCardNumber[32]{};
  NET_DVR_TIME struStartTime{};
  NET_DVR_TIME struStopTime{};
  BYTE byDrawFrame{};
  BYTE byFindType{};
  BYTE byQuickSearch{};
  BYTE bySpecialFindInfoType{};
  DWORD dwVolumeNum{};
  BYTE byWorkingDeviceGUID[16]{};
  BYTE byResSpecial[160]{};
  BYTE byStreamType{};
  BYTE byAudioFile{};
  BYTE byRes2[30]{};
};

struct NET_DVR_FINDDATA_V40 {
  char sFileName[100]{};
  NET_DVR_TIME struStartTime{};
  NET_DVR_TIME struStopTime{};
  DWORD dwFileSize{};
  char sCardNum[32]{};
  BYTE byLocked{};
  BYTE byFileType{};
  BYTE byQuickSearch{};
  BYTE byRes{};
  DWORD dwFileIndex{};
  BYTE byStreamType{};
  BYTE byRes1[127]{};
};'''
    text = replace_once(text, struct_anchor, structs, "stub event search structs")
    function_anchor = """inline BOOL NET_DVR_StopPlayBack(LONG) { return TRUE; }"""
    functions = function_anchor + r'''
inline LONG NET_DVR_FindFileByEvent_V40(LONG, NET_DVR_SEARCH_EVENT_PARAM_V40*) { return 1; }
inline LONG NET_DVR_FindNextEvent_V40(LONG, NET_DVR_SEARCH_EVENT_RET_V40*) { return NET_DVR_NOMOREFILE; }
inline BOOL NET_DVR_FindClose_V30(LONG) { return TRUE; }
inline LONG NET_DVR_FindFile_V40(LONG, NET_DVR_FILECOND_V40*) { return 1; }
inline LONG NET_DVR_FindNextFile_V40(LONG, NET_DVR_FINDDATA_V40*) { return NET_DVR_NOMOREFILE; }'''
    text = replace_once(text, function_anchor, functions, "stub event search functions")
    path.write_text(text, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-dir", default=".")
    args = parser.parse_args()
    root = Path(args.project_dir).resolve()
    patch_config(root / "src/config.ts")
    patch_runtime_events(root / "src/nativeSdk/runtimeEvents.ts")
    patch_device_runtime(root / "src/nativeSdk/deviceRuntime.ts")
    patch_event_store(root / "src/events/eventStore.ts")
    patch_event_collector(root / "src/nativeSdk/eventCollector.ts")
    patch_cpp_worker(root / "native-sdk/hik_sdk_device_worker.cpp")
    patch_stub(root / "test/native-sdk/HCNetSDK.h")
    print("Native HCNetSDK historical event reconciliation prepared")
    print("Event callbacks remain realtime; historical scan fills SQLite gaps for every DVR/channel")


if __name__ == "__main__":
    main()
