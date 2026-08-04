import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';
import { config } from '../config.js';

const require = createRequire(import.meta.url);

type SQLiteStatement = {
  run(...params: unknown[]): { changes: number | bigint; lastInsertRowid: number | bigint };
  get(...params: unknown[]): Record<string, unknown> | undefined;
  all(...params: unknown[]): Array<Record<string, unknown>>;
};

type SQLiteDatabase = {
  exec(sql: string): void;
  prepare(sql: string): SQLiteStatement;
  close(): void;
};

const { DatabaseSync } = require('node:sqlite') as {
  DatabaseSync: new (filename: string) => SQLiteDatabase;
};

export type HikvisionEventInput = {
  id?: string;
  event_hash?: string;
  channel_id: string;
  event_type: string;
  event_state?: string | null;
  topic?: string | null;
  source_name?: string | null;
  occurred_at?: string | Date | number | null;
  data?: Record<string, unknown> | null;
};

export type HikvisionEvent = {
  id: string;
  camera_id: string;
  stream_name: string;
  event_type: string;
  event_state: string | null;
  topic: string | null;
  source_name: string | null;
  occurred_at: string;
  created_at: string;
  data: Record<string, unknown>;
};

let db: SQLiteDatabase | null = null;
let dbPath = '';
let initializedAt: string | null = null;
let lastInsertAt: string | null = null;
let lastError: string | null = null;

function intEnv(name: string, fallback: number, min: number, max: number): number {
  const parsed = Number(process.env[name] ?? fallback);
  return Number.isFinite(parsed) ? Math.max(min, Math.min(max, Math.trunc(parsed))) : fallback;
}

function eventDbPath(): string {
  return String(process.env.HIK_EVENT_DB || '').trim() || path.join(config.root, 'events', 'events.sqlite3');
}

function normalizeOccurredAt(value: HikvisionEventInput['occurred_at']): number {
  if (value instanceof Date) return Number.isFinite(value.getTime()) ? value.getTime() : Date.now();
  if (typeof value === 'number') return Number.isFinite(value) ? value : Date.now();
  if (typeof value === 'string' && value.trim()) {
    const parsed = Date.parse(value);
    if (Number.isFinite(parsed)) return parsed;
  }
  return Date.now();
}

function stableJson(value: unknown): string {
  if (value === null || value === undefined) return 'null';
  if (Array.isArray(value)) return `[${value.map(stableJson).join(',')}]`;
  if (typeof value === 'object') {
    const obj = value as Record<string, unknown>;
    return `{${Object.keys(obj).sort().map((key) => `${JSON.stringify(key)}:${stableJson(obj[key])}`).join(',')}}`;
  }
  return JSON.stringify(value);
}

function hashEvent(input: HikvisionEventInput, occurredAtMs: number, data: Record<string, unknown>): string {
  if (input.event_hash) return input.event_hash;
  return crypto.createHash('sha256').update([
    input.channel_id,
    input.event_type,
    input.event_state || '',
    input.topic || '',
    input.source_name || '',
    new Date(occurredAtMs).toISOString(),
    stableJson(data)
  ].join('|')).digest('hex');
}

function database(): SQLiteDatabase {
  if (!db) initializeHikvisionEventStore();
  if (!db) throw new Error('Hikvision event database did not initialize');
  return db;
}

export function initializeHikvisionEventStore(): void {
  if (db) return;
  dbPath = eventDbPath();
  fs.mkdirSync(path.dirname(dbPath), { recursive: true, mode: 0o750 });
  try {
    const opened = new DatabaseSync(dbPath);
    opened.exec(`
      PRAGMA journal_mode = WAL;
      PRAGMA synchronous = NORMAL;
      PRAGMA foreign_keys = ON;
      PRAGMA busy_timeout = 5000;
      CREATE TABLE IF NOT EXISTS camera_events (
        id TEXT PRIMARY KEY,
        event_hash TEXT NOT NULL,
        channel_id TEXT NOT NULL,
        event_type TEXT NOT NULL,
        event_state TEXT,
        topic TEXT,
        source_name TEXT,
        occurred_at_ms INTEGER NOT NULL,
        created_at_ms INTEGER NOT NULL,
        data_json TEXT NOT NULL
      );
      CREATE UNIQUE INDEX IF NOT EXISTS camera_events_channel_hash
        ON camera_events(channel_id, event_hash);
      CREATE INDEX IF NOT EXISTS camera_events_channel_time
        ON camera_events(channel_id, occurred_at_ms);
      CREATE INDEX IF NOT EXISTS camera_events_type_time
        ON camera_events(event_type, occurred_at_ms);
    `);
    db = opened;
    initializedAt = new Date().toISOString();
    lastError = null;
    console.log('[hikvision-events] store initialized', { path: dbPath });
  } catch (error) {
    lastError = error instanceof Error ? error.message : String(error);
    throw error;
  }
}

export function appendHikvisionEvent(input: HikvisionEventInput): { inserted: boolean; id: string } {
  if (!input.channel_id || !input.event_type) throw new Error('channel_id and event_type are required');
  const store = database();
  const data = input.data && typeof input.data === 'object' ? input.data : {};
  const occurredAtMs = normalizeOccurredAt(input.occurred_at);
  const createdAtMs = Date.now();
  const eventHash = hashEvent(input, occurredAtMs, data);
  const id = input.id || crypto.randomUUID();
  const result = store.prepare(`
    INSERT OR IGNORE INTO camera_events(
      id, event_hash, channel_id, event_type, event_state, topic,
      source_name, occurred_at_ms, created_at_ms, data_json
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
  `).run(
    id, eventHash, input.channel_id, input.event_type, input.event_state ?? null,
    input.topic ?? null, input.source_name ?? null, occurredAtMs, createdAtMs, JSON.stringify(data)
  );
  const inserted = Number(result.changes) > 0;
  if (inserted) lastInsertAt = new Date(createdAtMs).toISOString();
  lastError = null;
  return { inserted, id };
}

function rowToEvent(row: Record<string, unknown>): HikvisionEvent {
  let data: Record<string, unknown> = {};
  try {
    const parsed = JSON.parse(String(row.data_json || '{}'));
    if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) data = parsed;
  } catch {
    data = { _invalid_json: true };
  }
  const channelId = String(row.channel_id);
  return {
    id: String(row.id),
    camera_id: channelId,
    stream_name: channelId,
    event_type: String(row.event_type),
    event_state: row.event_state == null ? null : String(row.event_state),
    topic: row.topic == null ? null : String(row.topic),
    source_name: row.source_name == null ? null : String(row.source_name),
    occurred_at: new Date(Number(row.occurred_at_ms)).toISOString(),
    created_at: new Date(Number(row.created_at_ms)).toISOString(),
    data
  };
}

export function listHikvisionEvents(params: {
  channelId: string;
  start: Date;
  end: Date;
  type?: string;
  limit?: number;
}): HikvisionEvent[] {
  const values: unknown[] = [params.channelId, params.start.getTime(), params.end.getTime()];
  let typeClause = '';
  if (params.type) {
    typeClause = ' AND event_type = ?';
    values.push(params.type);
  }
  values.push(Math.max(1, Math.min(5000, Math.trunc(params.limit || 5000))));
  return database().prepare(`
    SELECT id, channel_id, event_type, event_state, topic, source_name,
           occurred_at_ms, created_at_ms, data_json
      FROM camera_events
     WHERE channel_id = ? AND occurred_at_ms >= ? AND occurred_at_ms <= ?${typeClause}
     ORDER BY occurred_at_ms ASC LIMIT ?
  `).all(...values).map(rowToEvent);
}

export function summarizeHikvisionEvents(params: { channelId: string; start: Date; end: Date }) {
  return database().prepare(`
    SELECT CAST(occurred_at_ms / 60000 AS INTEGER) * 60000 AS bucket_ms,
           count(*) AS count, group_concat(DISTINCT event_type) AS types
      FROM camera_events
     WHERE channel_id = ? AND occurred_at_ms >= ? AND occurred_at_ms <= ?
     GROUP BY bucket_ms ORDER BY bucket_ms ASC
  `).all(params.channelId, params.start.getTime(), params.end.getTime()).map((row) => ({
    bucket: new Date(Number(row.bucket_ms)).toISOString(),
    count: Number(row.count || 0),
    types: String(row.types || '').split(',').map((value) => value.trim()).filter(Boolean).sort()
  }));
}

export function cleanupHikvisionEvents(): number {
  const retentionDays = intEnv('HIK_EVENT_RETENTION_DAYS', 30, 1, 3650);
  const cutoff = Date.now() - retentionDays * 24 * 3600_000;
  const result = database().prepare('DELETE FROM camera_events WHERE occurred_at_ms < ?').run(cutoff);
  return Number(result.changes);
}

export function getHikvisionEventStoreHealth() {
  try {
    const row = database().prepare(`
      SELECT count(*) AS total, min(occurred_at_ms) AS first_ms, max(occurred_at_ms) AS last_ms
      FROM camera_events
    `).get() || {};
    return {
      ok: true,
      storage: 'sqlite',
      path: dbPath,
      total_events: Number(row.total || 0),
      first_event_at: row.first_ms ? new Date(Number(row.first_ms)).toISOString() : null,
      last_event_at: row.last_ms ? new Date(Number(row.last_ms)).toISOString() : null,
      initialized_at: initializedAt,
      last_insert_at: lastInsertAt,
      last_error: lastError,
      retention_days: intEnv('HIK_EVENT_RETENTION_DAYS', 30, 1, 3650)
    };
  } catch (error) {
    lastError = error instanceof Error ? error.message : String(error);
    return { ok: false, storage: 'sqlite', path: dbPath || eventDbPath(), last_error: lastError };
  }
}

export function closeHikvisionEventStore(): void {
  if (!db) return;
  db.close();
  db = null;
}
