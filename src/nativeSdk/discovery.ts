import type {
  HikvisionChannel,
  HikvisionDeviceConfig,
  HikvisionStreamSettings,
  StreamType
} from '../types.js';
import { probeNativeDevice } from './client.js';

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

function buildChannel(
  device: HikvisionDeviceConfig,
  physical: number,
  sdkChannel: number,
  previous?: HikvisionChannel
): HikvisionChannel {
  const id = `${device.id}:${physical}`;
  const override = device.channel_overrides?.[id] || device.channel_overrides?.[String(physical)] || {};
  const oldStreams = previous?.streams || [];
  const streams = [
    syntheticStream(physical, 'main', oldStreams.find((item) => item.stream_type === 'main')),
    syntheticStream(physical, 'sub', oldStreams.find((item) => item.stream_type === 'sub'))
  ];
  const primary = override.primary_stream_id
    || previous?.primary_stream_id
    || streams[0]!.id;
  return {
    id,
    device_id: device.id,
    physical_channel: physical,
    sdk_channel: sdkChannel,
    name: previous?.name || `${device.name} channel ${physical}`,
    online: true,
    enabled: override.enabled ?? previous?.enabled ?? true,
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

export function discoverNativeHikvisionDevice(
  device: HikvisionDeviceConfig,
  previousChannels: HikvisionChannel[] = []
): NativeDiscoveryResult {
  const probe = probeNativeDevice(device);
  const previous = new Map(previousChannels.map((channel) => [channel.physical_channel, channel]));
  const channels: HikvisionChannel[] = [];

  const analogStart = Math.max(1, Number(probe.analog_start || 1));
  const analogCount = Math.max(0, Number(probe.analog_count || 0));
  for (let index = 0; index < analogCount; index += 1) {
    const physical = index + 1;
    channels.push(buildChannel(device, physical, analogStart + index, previous.get(physical)));
  }

  const digitalStart = Math.max(1, Number(probe.digital_start || 1));
  const digitalCount = Math.max(0, Number(probe.digital_count || 0));
  for (let index = 0; index < digitalCount; index += 1) {
    // HCNetSDK uses lChannel=33+ for many NVR digital channels, while the UI
    // should continue to expose the human channel sequence after analog inputs.
    const physical = analogCount + index + 1;
    channels.push(buildChannel(device, physical, digitalStart + index, previous.get(physical)));
  }

  if (!channels.length) throw new Error('HCNetSDK login succeeded but device reported no channels');
  return {
    device_info: {
      serialNumber: probe.serial,
      analog_start: probe.analog_start,
      analog_count: probe.analog_count,
      digital_start: probe.digital_start,
      digital_count: probe.digital_count
    },
    capabilities: {
      transport: 'hcnet-private-sdk',
      sdk_private_login: true,
      live_callback: true,
      archive_search: true,
      archive_playback: true,
      alarm_channel: true,
      main_proto: probe.main_proto,
      sub_proto: probe.sub_proto
    },
    channels
  };
}
