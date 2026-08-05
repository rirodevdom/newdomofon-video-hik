import type { HikvisionNodeService } from '../service.js';
import { appendHikvisionEvent, cleanupHikvisionEvents } from '../events/eventStore.js';
import { onNativeRuntimeAlarm, type NativeRuntimeAlarm } from './runtimeEvents.js';

function enabled(): boolean {
  const raw = String(process.env.HIK_EVENTS_ENABLED ?? 'true').trim().toLowerCase();
  return ['1', 'true', 'yes', 'on'].includes(raw);
}

export class NativeSdkEventCollector {
  private readonly lastEventAt = new Map<string, number>();
  private cleanupTimer: NodeJS.Timeout | null = null;
  private unsubscribe: (() => void) | null = null;

  constructor(private readonly service: HikvisionNodeService) {}

  start(): void {
    if (!enabled()) {
      console.log('[hikvision-events] native HCNetSDK collector disabled');
      return;
    }
    console.log('[hikvision-events] grouped HCNetSDK alarm consumer enabled');
    this.unsubscribe = onNativeRuntimeAlarm((deviceId, alarm) => this.consumeAlarm(deviceId, alarm));
    cleanupHikvisionEvents();
    this.cleanupTimer = setInterval(() => {
      try { cleanupHikvisionEvents(); }
      catch (error) { console.warn('[hikvision-events] retention failed', error instanceof Error ? error.message : error); }
    }, 60 * 60 * 1000);
    this.cleanupTimer.unref?.();
  }

  stop(): void {
    if (this.cleanupTimer) clearInterval(this.cleanupTimer);
    this.cleanupTimer = null;
    this.unsubscribe?.();
    this.unsubscribe = null;
  }

  private consumeAlarm(deviceId: string, alarm: NativeRuntimeAlarm): void {
    const snapshot = this.service.listDevices(false).find((item) => item.config.id === deviceId);
    if (!snapshot) return;
    const physical = Number(alarm.physical_channel || 0);
    if (!Number.isInteger(physical) || physical <= 0) return;
    const channel = snapshot.channels.find((item) => item.physical_channel === physical || item.sdk_channel === physical);
    if (!channel) {
      console.warn(`[hikvision-events:${deviceId}] HCNetSDK alarm channel not mapped: ${physical}`);
      return;
    }
    const eventType = String(alarm.event_type || 'hikvision_alarm');
    const occurredAt = String(alarm.occurred_at || new Date().toISOString());
    const dedupeKey = `${channel.id}|${eventType}|${alarm.event_code || 0}`;
    const eventMs = Date.parse(occurredAt) || Date.now();
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
