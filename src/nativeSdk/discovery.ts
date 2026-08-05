import type {
  HikvisionChannel,
  HikvisionDeviceConfig,
  HikvisionStreamSettings,
  StreamType
} from '../types.js';
import { probeNativeChannels, probeNativeDevice } from './client.js';

function syntheticStream(physical: number, type: StreamType, previous?: HikvisionStreamSettings): HikvisionStreamSettings {
  const suffix = type === 'main' ? 1 : type === 'sub' ? 2 : 3;
  return previous ? { ...previous } : {
    id: `${physical}${String(suffix).padStart(2, '0')}`,
    stream_type: type,
    enabled: true,
    name: type,
    video_input_channel_id: physical,
    video_codec: null,
    width: null,
    height: null,
    frame_rate: null,
    bitrate_kbps: null,
    bitrate_mode: null,
    gop: null,
    audio_codec: null,
    raw: { source: 'hcnet-private-sdk' }
  };
}

export function resolveNativeChannelEnabled(overrideEnabled: boolean | undefined): boolean {
  // Legacy ISAPI discovery mixed device-reported channel status into the
  // persisted `enabled` field. Native HCNetSDK discovery must not inherit that
  // historical value, otherwise a previously device-reported false can keep a
  // working SDK channel disabled forever. Only an explicit master override may
  // disable a configured native channel; online/offline is tracked separately.
  return overrideEnabled ?? true;
}

function buildChannel(
  device: HikvisionDeviceConfig,
  physical: number,
  sdkChannel: number,
  online: boolean | null,
  previous?: HikvisionChannel
): HikvisionChannel {
  const id = `${device.id}:${physical}`;
  const override = device.channel_overrides?.[id] || device.channel_overrides?.[String(physical)] || {};
  const oldStreams = previous?.streams || [];
  const streams = [
    syntheticStream(physical, 'main', oldStreams.find((item) => item.stream_type === 'main')),
    syntheticStream(physical, 'sub', oldStreams.find((item) => item.stream_type === 'sub'))
  ];
  const primary = override.primary_stream_id || previous?.primary_stream_id || streams[0]!.id;
  return {
    id,
    device_id: device.id,
    physical_channel: physical,
    sdk_channel: sdkChannel,
    name: previous?.name || `${device.name} channel ${physical}`,
    online,
    enabled: resolveNativeChannelEnabled(override.enabled),
    primary_stream_id: primary,
    archive_track_ids: previous?.archive_track_ids?.length ? previous.archive_track_ids : [streams[0]!.id],
    archive_storage: override.archive_storage || device.archive_storage,
    retention_days: override.retention_days || device.retention_days,
    streams,
    discovered_at: new Date().toISOString()
  };
}

export interface NativeDiscoveryResult {
  device_info: Record<string, unknown>;
  capabilities: Record<string, unknown>;
  channels: HikvisionChannel[];
}

export async function discoverNativeHikvisionDevice(
  device: HikvisionDeviceConfig,
  previousChannels: HikvisionChannel[] = []
): Promise<NativeDiscoveryResult> {
  const [probe, inventory] = await Promise.all([
    probeNativeDevice(device),
    probeNativeChannels(device)
  ]);
  const previous = new Map(previousChannels.map((channel) => [channel.physical_channel, channel]));
  const channels = inventory.channels
    .filter((item) => item.configured)
    .sort((left, right) => left.physical_channel - right.physical_channel)
    .map((item) => buildChannel(
      device,
      item.physical_channel,
      item.sdk_channel,
      item.online,
      previous.get(item.physical_channel)
    ));

  if (!channels.length) throw new Error('HCNetSDK login succeeded but device reported no configured channels');
  return {
    device_info: {
      serialNumber: probe.serial,
      analog_start: probe.analog_start,
      analog_count: probe.analog_count,
      digital_start: probe.digital_start,
      digital_count: probe.digital_count,
      configured_channels: channels.length,
      channel_config_available: inventory.config_available
    },
    capabilities: {
      transport: 'hcnet-private-sdk',
      sdk_private_login: true,
      live_callback: true,
      archive_search: true,
      archive_playback: true,
      alarm_channel: true,
      configured_channel_inventory: true,
      main_proto: probe.main_proto,
      sub_proto: probe.sub_proto
    },
    channels
  };
}
