import http from 'node:http';
import https from 'node:https';
import { XMLParser } from 'fast-xml-parser';
import { config } from '../config.js';
import { digestAuthorization, parseDigestChallenge } from '../isapi/digest.js';
import type { HikvisionDeviceConfig, HikvisionDeviceSnapshot } from '../types.js';
import type { HikvisionNodeService } from '../service.js';
import { appendHikvisionEvent, cleanupHikvisionEvents } from './eventStore.js';

interface CollectorSession {
  abort: AbortController;
  key: string;
}

const ALERT_URI = '/ISAPI/Event/notification/alertStream';
const parser = new XMLParser({ ignoreAttributes: false, attributeNamePrefix: '@_' });

function enabled(): boolean {
  const raw = String(process.env.HIK_EVENTS_ENABLED ?? 'true').trim().toLowerCase();
  return ['1', 'true', 'yes', 'on'].includes(raw);
}

function reconnectMs(): number {
  const value = Number(process.env.HIK_EVENTS_RECONNECT_MS || 5000);
  return Number.isFinite(value) ? Math.max(1000, Math.min(60_000, Math.trunc(value))) : 5000;
}

function basicAuthorization(device: HikvisionDeviceConfig): string {
  return `Basic ${Buffer.from(`${device.username}:${device.password}`).toString('base64')}`;
}

function openRequest(
  device: HikvisionDeviceConfig,
  authorization: string,
  signal: AbortSignal
): Promise<http.IncomingMessage> {
  const protocol = device.scheme === 'https' ? https : http;
  return new Promise((resolve, reject) => {
    const req = protocol.request({
      protocol: `${device.scheme}:`,
      hostname: device.host,
      port: device.isapi_port,
      method: 'GET',
      path: ALERT_URI,
      headers: {
        accept: 'application/xml,text/xml,multipart/mixed,*/*',
        authorization,
        'user-agent': 'NewDomofon-Hikvision-Events/1.0',
        connection: 'keep-alive'
      },
      ...(device.scheme === 'https' ? { rejectUnauthorized: device.reject_unauthorized_tls } : {})
    }, (res) => {
      req.setTimeout(0);
      resolve(res);
    });
    const abort = () => req.destroy(new Error('Hikvision alertStream aborted'));
    signal.addEventListener('abort', abort, { once: true });
    req.setTimeout(config.requestTimeoutMs, () => req.destroy(new Error(`alertStream headers timeout after ${config.requestTimeoutMs} ms`)));
    req.once('error', reject);
    req.end();
  });
}

async function openAlertStream(device: HikvisionDeviceConfig, signal: AbortSignal): Promise<http.IncomingMessage> {
  let response = await openRequest(device, basicAuthorization(device), signal);
  if (response.statusCode !== 401) return response;

  const challenge = String(response.headers['www-authenticate'] || '');
  response.resume();
  if (!/^Digest\s/i.test(challenge)) return response;

  const authorization = digestAuthorization(
    parseDigestChallenge(challenge),
    'GET',
    ALERT_URI,
    device.username,
    device.password
  );
  return openRequest(device, authorization, signal);
}

function findText(value: unknown, keys: string[]): string {
  if (!value || typeof value !== 'object') return '';
  if (Array.isArray(value)) {
    for (const item of value) {
      const found = findText(item, keys);
      if (found) return found;
    }
    return '';
  }
  const object = value as Record<string, unknown>;
  for (const key of keys) {
    const exact = object[key];
    if (exact !== undefined && exact !== null && typeof exact !== 'object') return String(exact);
  }
  for (const item of Object.values(object)) {
    const found = findText(item, keys);
    if (found) return found;
  }
  return '';
}

function physicalChannel(raw: string): number | null {
  const digits = String(raw || '').match(/\d+/)?.[0];
  if (!digits) return null;
  const value = Number(digits);
  if (!Number.isFinite(value) || value <= 0) return null;
  // Some NVRs report streaming-channel style IDs such as 701/702 instead of
  // the physical channel number. Alert messages with 1..99 stay unchanged.
  return value >= 100 ? Math.floor(value / 100) : value;
}

function eventChannel(snapshot: HikvisionDeviceSnapshot, root: Record<string, unknown>) {
  const dyn = findText(root, ['dynChannelID']);
  const regular = findText(root, ['channelID', 'channel']);
  const candidates = [dyn, regular].map(physicalChannel).filter((value): value is number => value !== null);
  for (const physical of candidates) {
    const channel = snapshot.channels.find((item) => item.physical_channel === physical);
    if (channel) return channel;
  }
  return null;
}

function isHeartbeat(eventType: string, eventState: string): boolean {
  return eventType.toLowerCase() === 'videoloss' && eventState.toLowerCase() === 'inactive';
}

async function consumeDevice(snapshot: HikvisionDeviceSnapshot, abort: AbortController): Promise<void> {
  while (!abort.signal.aborted) {
    try {
      const response = await openAlertStream(snapshot.config, abort.signal);
      if ((response.statusCode || 0) < 200 || (response.statusCode || 0) >= 300) {
        response.resume();
        throw new Error(`alertStream HTTP ${response.statusCode || 0}`);
      }

      console.log(`[hikvision-events:${snapshot.config.id}] alertStream connected`);
      let buffer = '';
      for await (const chunk of response) {
        if (abort.signal.aborted) break;
        buffer += Buffer.isBuffer(chunk) ? chunk.toString('utf8') : String(chunk);
        let match: RegExpMatchArray | null;
        const expression = /<EventNotificationAlert\b[\s\S]*?<\/EventNotificationAlert>/i;
        while ((match = buffer.match(expression))) {
          buffer = buffer.slice((match.index || 0) + match[0].length);
          try {
            const parsed = parser.parse(match[0]);
            const root = (parsed.EventNotificationAlert || parsed) as Record<string, unknown>;
            const eventType = findText(root, ['eventType']) || 'hikvision.event';
            const eventState = findText(root, ['eventState']);
            if (isHeartbeat(eventType, eventState)) continue;
            const channel = eventChannel(snapshot, root);
            if (!channel) {
              console.warn(`[hikvision-events:${snapshot.config.id}] event channel was not mapped`, {
                channelID: findText(root, ['channelID']),
                dynChannelID: findText(root, ['dynChannelID']),
                eventType
              });
              continue;
            }
            const occurredAt = findText(root, ['dateTime']) || new Date().toISOString();
            appendHikvisionEvent({
              channel_id: channel.id,
              event_type: eventType,
              event_state: eventState || null,
              topic: eventType,
              source_name: 'hikvision.alertStream',
              occurred_at: occurredAt,
              data: {
                device_id: snapshot.config.id,
                device_name: snapshot.config.name,
                physical_channel: channel.physical_channel,
                channel_name: channel.name,
                channelID: findText(root, ['channelID']),
                dynChannelID: findText(root, ['dynChannelID']),
                event_description: findText(root, ['eventDescription']) || null,
                active_post_count: findText(root, ['activePostCount']) || null
              }
            });
          } catch (error) {
            console.warn(`[hikvision-events:${snapshot.config.id}] parse/store failed`, error instanceof Error ? error.message : error);
          }
        }
        if (buffer.length > 2 * 1024 * 1024) buffer = buffer.slice(-128 * 1024);
      }
      if (!abort.signal.aborted) console.warn(`[hikvision-events:${snapshot.config.id}] alertStream disconnected`);
    } catch (error) {
      if (!abort.signal.aborted) {
        console.warn(`[hikvision-events:${snapshot.config.id}] alertStream failed`, error instanceof Error ? error.message : error);
      }
    }
    if (!abort.signal.aborted) await new Promise((resolve) => setTimeout(resolve, reconnectMs()));
  }
}

export class HikvisionEventCollector {
  private readonly sessions = new Map<string, CollectorSession>();
  private syncTimer: NodeJS.Timeout | null = null;
  private cleanupTimer: NodeJS.Timeout | null = null;

  constructor(private readonly service: HikvisionNodeService) {}

  start(): void {
    if (!enabled()) {
      console.log('[hikvision-events] collector disabled');
      return;
    }
    console.log('[hikvision-events] collector enabled');
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
    if (this.syncTimer) clearInterval(this.syncTimer);
    if (this.cleanupTimer) clearInterval(this.cleanupTimer);
    this.syncTimer = null;
    this.cleanupTimer = null;
    for (const session of this.sessions.values()) session.abort.abort();
    this.sessions.clear();
  }

  private async reconcile(): Promise<void> {
    const devices = this.service.listDevices(false).filter((item) => item.config.enabled && item.channels.some((channel) => channel.enabled));
    const wanted = new Set(devices.map((item) => item.config.id));
    for (const [id, session] of this.sessions) {
      if (!wanted.has(id)) {
        session.abort.abort();
        this.sessions.delete(id);
      }
    }
    for (const snapshot of devices) {
      const key = JSON.stringify({
        config: snapshot.config,
        channels: snapshot.channels.map((channel) => [channel.id, channel.physical_channel, channel.enabled])
      });
      const existing = this.sessions.get(snapshot.config.id);
      if (existing?.key === key) continue;
      existing?.abort.abort();
      const abort = new AbortController();
      this.sessions.set(snapshot.config.id, { abort, key });
      void consumeDevice(snapshot, abort);
    }
  }
}
