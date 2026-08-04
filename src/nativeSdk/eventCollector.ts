import type { ChildProcessWithoutNullStreams } from 'node:child_process';
import type { HikvisionDeviceSnapshot } from '../types.js';
import type { HikvisionNodeService } from '../service.js';
import { appendHikvisionEvent, cleanupHikvisionEvents } from '../events/eventStore.js';
import { spawnNativeStream } from './client.js';

interface CollectorSession {
  process: ChildProcessWithoutNullStreams;
  key: string;
  buffer: string;
  stopped: boolean;
}

interface NativeAlarm {
  command?: number;
  event_type?: string;
  event_state?: string;
  physical_channel?: number;
  alarm_type?: number;
  event_code?: number;
  occurred_at?: string;
}

function enabled(): boolean {
  const raw = String(process.env.HIK_EVENTS_ENABLED ?? 'true').trim().toLowerCase();
  return ['1', 'true', 'yes', 'on'].includes(raw);
}

function reconnectMs(): number {
  const value = Number(process.env.HIK_EVENTS_RECONNECT_MS || 5000);
  return Number.isFinite(value) ? Math.max(1000, Math.min(60_000, Math.trunc(value))) : 5000;
}

export class NativeSdkEventCollector {
  private readonly sessions = new Map<string, CollectorSession>();
  private readonly lastEventAt = new Map<string, number>();
  private syncTimer: NodeJS.Timeout | null = null;
  private cleanupTimer: NodeJS.Timeout | null = null;
  private stopped = false;

  constructor(private readonly service: HikvisionNodeService) {}

  start(): void {
    if (!enabled()) {
      console.log('[hikvision-events] native HCNetSDK collector disabled');
      return;
    }
    console.log('[hikvision-events] native HCNetSDK collector enabled');
    this.stopped = false;
    void this.reconcile();
    this.syncTimer = setInterval(() => { void this.reconcile(); }, 30_000);
    this.syncTimer.unref?.();
    cleanupHikvisionEvents();
    this.cleanupTimer = setInterval(() => {
      try { cleanupHikvisionEvents(); }
      catch (error) { console.warn('[hikvision-events] retention failed', error instanceof Error ? error.message : error); }
    }, 60 * 60 * 1000);
    this.cleanupTimer.unref?.();
  }

  stop(): void {
    this.stopped = true;
    if (this.syncTimer) clearInterval(this.syncTimer);
    if (this.cleanupTimer) clearInterval(this.cleanupTimer);
    this.syncTimer = null;
    this.cleanupTimer = null;
    for (const session of this.sessions.values()) {
      session.stopped = true;
      session.process.kill('SIGTERM');
    }
    this.sessions.clear();
  }

  private async reconcile(): Promise<void> {
    if (this.stopped) return;
    const devices = this.service.listDevices(false).filter((item) => item.config.enabled && item.channels.some((channel) => channel.enabled));
    const wanted = new Set(devices.map((item) => item.config.id));
    for (const [id, session] of this.sessions) {
      if (!wanted.has(id)) {
        session.stopped = true;
        session.process.kill('SIGTERM');
        this.sessions.delete(id);
      }
    }
    for (const snapshot of devices) {
      const key = JSON.stringify({
        config: [snapshot.config.host, snapshot.config.username, snapshot.config.password],
        channels: snapshot.channels.map((channel) => [channel.id, channel.physical_channel, channel.sdk_channel, channel.enabled])
      });
      const current = this.sessions.get(snapshot.config.id);
      if (current?.key === key && current.process.exitCode === null) continue;
      if (current) {
        current.stopped = true;
        current.process.kill('SIGTERM');
      }
      this.startDevice(snapshot, key);
    }
  }

  private startDevice(snapshot: HikvisionDeviceSnapshot, key: string): void {
    const channel = snapshot.channels.find((item) => item.enabled);
    if (!channel || this.stopped) return;
    const process = spawnNativeStream(snapshot.config, channel, 'events');
    const session: CollectorSession = { process, key, buffer: '', stopped: false };
    this.sessions.set(snapshot.config.id, session);
    console.log(`[hikvision-events:${snapshot.config.id}] HCNetSDK alarm channel started`);

    process.stdout.on('data', (chunk) => {
      session.buffer += String(chunk);
      for (;;) {
        const newline = session.buffer.indexOf('\n');
        if (newline < 0) break;
        const line = session.buffer.slice(0, newline).trim();
        session.buffer = session.buffer.slice(newline + 1);
        if (!line.startsWith('{')) continue;
        try { this.consumeAlarm(snapshot, JSON.parse(line) as NativeAlarm); }
        catch (error) { console.warn(`[hikvision-events:${snapshot.config.id}] native event parse failed`, error instanceof Error ? error.message : error); }
      }
      if (session.buffer.length > 1024 * 1024) session.buffer = session.buffer.slice(-64 * 1024);
    });
    let stderr = '';
    process.stderr.on('data', (chunk) => { stderr = `${stderr}\n${String(chunk)}`.slice(-3000); });
    process.once('exit', () => {
      if (this.sessions.get(snapshot.config.id) === session) this.sessions.delete(snapshot.config.id);
      if (session.stopped || this.stopped) return;
      console.warn(`[hikvision-events:${snapshot.config.id}] HCNetSDK alarm channel exited: ${stderr.trim()}`);
      const timer = setTimeout(() => {
        if (!this.stopped) void this.reconcile();
      }, reconnectMs());
      timer.unref?.();
    });
  }

  private consumeAlarm(snapshot: HikvisionDeviceSnapshot, alarm: NativeAlarm): void {
    const physical = Number(alarm.physical_channel || 0);
    if (!Number.isInteger(physical) || physical <= 0) return;
    const channel = snapshot.channels.find((item) => item.physical_channel === physical || item.sdk_channel === physical);
    if (!channel) {
      console.warn(`[hikvision-events:${snapshot.config.id}] HCNetSDK alarm channel not mapped: ${physical}`);
      return;
    }
    const eventType = String(alarm.event_type || 'hikvision_alarm');
    const occurredAt = String(alarm.occurred_at || new Date().toISOString());
    const dedupeKey = `${channel.id}|${eventType}|${alarm.event_code || 0}`;
    const eventMs = Date.parse(occurredAt) || Date.now();
    const previous = this.lastEventAt.get(dedupeKey) || 0;
    // V30 motion can be uploaded once per second while active. One marker per
    // two seconds is enough for the timeline and avoids flooding SQLite/UI.
    if (eventMs - previous < 2000) return;
    this.lastEventAt.set(dedupeKey, eventMs);
    appendHikvisionEvent({
      channel_id: channel.id,
      event_type: eventType,
      event_state: alarm.event_state || 'active',
      topic: eventType,
      source_name: 'hikvision.hcnetsdk',
      occurred_at: occurredAt,
      data: {
        device_id: snapshot.config.id,
        device_name: snapshot.config.name,
        physical_channel: channel.physical_channel,
        sdk_channel: channel.sdk_channel ?? channel.physical_channel,
        channel_name: channel.name,
        command: alarm.command ?? null,
        alarm_type: alarm.alarm_type ?? null,
        event_code: alarm.event_code ?? null
      }
    });
  }
}
