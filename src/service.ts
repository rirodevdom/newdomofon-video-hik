import { config } from './config.js';
import { discoverHikvisionDevice, fetchStreamingChannelSettings } from './isapi/discovery.js';
import { RecorderManager } from './media/recorderManager.js';
import { EncryptedStateStore } from './state/encryptedStore.js';
import type {
  HikvisionChannel,
  HikvisionDeviceConfig,
  HikvisionDeviceSnapshot,
  PersistedState,
  RecorderStatus
} from './types.js';
import { enforceLocalRetention } from './archive/localArchive.js';

export function resolveChannelOnlineStatus(
  isapiOnline: boolean | null,
  recorder: Pick<RecorderStatus, 'running' | 'restarts' | 'last_error'>
): boolean | null {
  if (isapiOnline !== null) return isapiOnline;
  if (recorder.running) return true;
  if (recorder.last_error || recorder.restarts > 0) return false;
  return null;
}

export class HikvisionNodeService {
  readonly recorderManager = new RecorderManager();
  private state: PersistedState = { version: 1, devices: [] };
  private syncTimer: NodeJS.Timeout | null = null;
  private retentionTimer: NodeJS.Timeout | null = null;

  constructor(private readonly store: EncryptedStateStore) {}

  async initialize(): Promise<void> {
    this.state = await this.store.load();
    await this.reconcileRecorders();
    this.syncTimer = setInterval(() => { void this.syncAll(); }, config.syncIntervalSeconds * 1000);
    this.syncTimer.unref?.();
    this.retentionTimer = setInterval(() => { void this.runRetention(); }, 60 * 60 * 1000);
    this.retentionTimer.unref?.();
    await this.runRetention();
  }

  shutdown(): void {
    if (this.syncTimer) clearInterval(this.syncTimer);
    if (this.retentionTimer) clearInterval(this.retentionTimer);
    this.recorderManager.stopAll();
  }

  listDevices(redactSecrets = true): HikvisionDeviceSnapshot[] {
    return this.state.devices.map((device) => this.snapshotView(device, redactSecrets));
  }

  getDevice(id: string, redactSecrets = true): HikvisionDeviceSnapshot | null {
    const item = this.state.devices.find((device) => device.config.id === id);
    return item ? this.snapshotView(item, redactSecrets) : null;
  }

  private channelView(channel: HikvisionChannel): HikvisionChannel {
    const copy = structuredClone(channel);
    copy.online = resolveChannelOnlineStatus(copy.online, this.recorderManager.status(copy.id));
    return copy;
  }

  private snapshotView(snapshot: HikvisionDeviceSnapshot, redactSecrets: boolean): HikvisionDeviceSnapshot {
    const copy = structuredClone(snapshot);
    copy.channels = copy.channels.map((channel) => this.channelView(channel));
    if (redactSecrets) copy.config.password = copy.config.password ? '***' : '';
    return copy;
  }

  findChannel(channelId: string): { device: HikvisionDeviceSnapshot; channel: HikvisionChannel } | null {
    for (const device of this.state.devices) {
      const channel = device.channels.find((item) => item.id === channelId);
      if (channel) return { device, channel };
    }
    return null;
  }

  allChannels(): Array<{ device_id: string; device_name: string; channel: HikvisionChannel }> {
    return this.state.devices.flatMap((device) => device.channels.map((channel) => ({
      device_id: device.config.id,
      device_name: device.config.name,
      channel: this.channelView(channel)
    })));
  }

  async upsertDevice(configValue: HikvisionDeviceConfig): Promise<HikvisionDeviceSnapshot> {
    const config = structuredClone(configValue);
    const current = this.state.devices.find((device) => device.config.id === config.id);
    const snapshot: HikvisionDeviceSnapshot = current || {
      config,
      device_info: {},
      capabilities: {},
      channels: [],
      last_sync_at: null,
      last_sync_error: null
    };
    snapshot.config = config;
    const index = this.state.devices.findIndex((device) => device.config.id === config.id);
    if (index >= 0) this.state.devices[index] = snapshot;
    else this.state.devices.push(snapshot);
    await this.persist();
    try {
      await this.syncDevice(config.id);
    } catch {
      // Configuration is intentionally retained. The returned snapshot exposes
      // last_sync_error so master can display the failure and retry later.
    }
    return this.getDevice(config.id)!;
  }

  async removeDevice(id: string): Promise<boolean> {
    const before = this.state.devices.length;
    this.state.devices = this.state.devices.filter((device) => device.config.id !== id);
    if (this.state.devices.length === before) return false;
    await this.persist();
    await this.reconcileRecorders();
    return true;
  }

  async syncDevice(id: string): Promise<HikvisionDeviceSnapshot> {
    const snapshot = this.state.devices.find((device) => device.config.id === id);
    if (!snapshot) throw new Error('Hikvision device not found');
    if (!snapshot.config.enabled) {
      snapshot.last_sync_error = 'Device disabled';
      await this.persist();
      await this.reconcileRecorders();
      return this.getDevice(id)!;
    }
    try {
      const result = await discoverHikvisionDevice(snapshot.config);
      snapshot.device_info = result.device_info;
      snapshot.capabilities = result.capabilities;
      snapshot.channels = result.channels;
      snapshot.last_sync_at = new Date().toISOString();
      snapshot.last_sync_error = null;
      await this.persist();
      await this.reconcileRecorders();
      return this.getDevice(id)!;
    } catch (error) {
      snapshot.last_sync_error = error instanceof Error ? error.message : String(error);
      await this.persist();
      throw error;
    }
  }

  async syncAll(): Promise<void> {
    for (const device of this.state.devices) {
      if (!device.config.enabled) continue;
      try {
        await this.syncDevice(device.config.id);
      } catch (error) {
        console.warn(`[sync:${device.config.id}] ${error instanceof Error ? error.message : error}`);
      }
    }
  }

  async reconcileMasterDevices(configs: HikvisionDeviceConfig[]): Promise<void> {
    const existing = new Map(this.state.devices.map((device) => [device.config.id, device]));
    const next: HikvisionDeviceSnapshot[] = [];
    const changedIds = new Set<string>();

    for (const configValue of configs) {
      const config = structuredClone(configValue);
      const current = existing.get(config.id);
      if (!current || JSON.stringify(current.config) !== JSON.stringify(config)) changedIds.add(config.id);
      next.push(current ? { ...current, config } : {
        config,
        device_info: {},
        capabilities: {},
        channels: [],
        last_sync_at: null,
        last_sync_error: null
      });
    }

    const nextIds = new Set(next.map((device) => device.config.id));
    const removed = this.state.devices.some((device) => !nextIds.has(device.config.id));
    this.state.devices = next;
    if (changedIds.size || removed) await this.persist();
    await this.reconcileRecorders();

    for (const device of this.state.devices) {
      if (!device.config.enabled || !changedIds.has(device.config.id)) continue;
      try {
        await this.syncDevice(device.config.id);
      } catch (error) {
        console.warn(`[master-sync:${device.config.id}] ${error instanceof Error ? error.message : error}`);
      }
    }
  }

  async restartRecorders(): Promise<void> {
    this.recorderManager.stopAll();
    await this.reconcileRecorders();
  }

  async refreshStreamSettings(channelId: string, streamId: string): Promise<HikvisionChannel> {
    const found = this.findChannel(channelId);
    if (!found) throw new Error('Hikvision channel not found');
    const settings = await fetchStreamingChannelSettings(found.device.config, streamId);
    const index = found.channel.streams.findIndex((stream) => stream.id === streamId);
    if (index >= 0) found.channel.streams[index] = settings;
    else found.channel.streams.push(settings);
    await this.persist();
    await this.reconcileRecorders();
    return this.channelView(found.channel);
  }

  private async persist(): Promise<void> {
    this.state.devices.sort((a, b) => a.config.name.localeCompare(b.config.name));
    await this.store.save(this.state);
  }

  private async reconcileRecorders(): Promise<void> {
    await this.recorderManager.reconcile(this.state.devices.map((device) => ({
      config: device.config,
      channels: device.channels
    })));
  }

  private async runRetention(): Promise<void> {
    for (const device of this.state.devices) {
      for (const channel of device.channels) {
        try {
          const deleted = await enforceLocalRetention(channel);
          if (deleted) console.log(`[retention:${channel.id}] deleted ${deleted} segments`);
        } catch (error) {
          console.warn(`[retention:${channel.id}] ${error instanceof Error ? error.message : error}`);
        }
      }
    }
  }
}
