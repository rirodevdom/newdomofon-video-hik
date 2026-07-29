import { config } from '../config.js';
import type {
  HikvisionChannel,
  HikvisionDeviceConfig,
  HikvisionStreamSettings,
  StreamType
} from '../types.js';
import { IsapiClient } from './client.js';
import {
  asArray,
  boolValue,
  findObjects,
  firstScalar,
  numberValue,
  objectValue,
  scalar,
  xmlParser
} from './xml.js';

interface ProxyStatus {
  physicalChannel: number;
  name: string | null;
  online: boolean | null;
  enabled: boolean | null;
  streamingProxyChannelId: string | null;
}

function streamType(id: string): StreamType {
  const suffix = Number(id) % 100;
  if (suffix === 1) return 'main';
  if (suffix === 2) return 'sub';
  if (suffix === 3) return 'third';
  return 'other';
}

function physicalChannelFromStreamId(id: string): number {
  const value = Number(id);
  return Number.isFinite(value) && value >= 100 ? Math.floor(value / 100) : value;
}

function normalizeCodec(value: unknown): string | null {
  const raw = scalar(value);
  return raw ? raw.trim().toUpperCase() : null;
}

function parseStream(block: Record<string, unknown>): HikvisionStreamSettings | null {
  const id = scalar(block.id);
  if (!id || !/^\d{3,5}$/.test(id)) return null;
  const video = objectValue(block.Video || block.video);
  const audio = objectValue(block.Audio || block.audio);
  const bitrate = numberValue(video.constantBitRate ?? video.maxBitRate ?? video.vbrUpperCap);
  const rawFrameRate = numberValue(video.maxFrameRate ?? video.frameRate);
  return {
    id,
    stream_type: streamType(id),
    enabled: boolValue(block.enabled),
    name: scalar(block.channelName ?? block.name),
    video_input_channel_id: numberValue(block.videoInputChannelID) ?? physicalChannelFromStreamId(id),
    video_codec: normalizeCodec(video.videoCodecType ?? video.codecType ?? video.videoEncoding),
    width: numberValue(video.videoResolutionWidth ?? video.width),
    height: numberValue(video.videoResolutionHeight ?? video.height),
    frame_rate: rawFrameRate != null && rawFrameRate > 100 ? rawFrameRate / 100 : rawFrameRate,
    bitrate_kbps: bitrate,
    bitrate_mode: scalar(video.videoQualityControlType ?? video.bitrateType),
    gop: numberValue(video.GovLength ?? video.govLength ?? video.GOPLength),
    audio_codec: normalizeCodec(audio.audioCompressionType ?? audio.audioCodecType ?? audio.codecType),
    raw: block
  };
}

function parseStreamingChannels(xml: string): HikvisionStreamSettings[] {
  const parsed = xmlParser.parse(xml);
  return findObjects(parsed, 'StreamingChannel')
    .map(parseStream)
    .filter((item): item is HikvisionStreamSettings => Boolean(item));
}

function statusValue(...values: unknown[]): boolean | null {
  for (const value of values) {
    const direct = boolValue(value);
    if (direct != null) return direct;

    const normalized = scalar(value)?.trim().toLowerCase().replace(/[^a-z0-9]+/g, '');
    if (!normalized) continue;
    if ([
      'connect', 'connected', 'normal', 'active', 'ok', 'ready',
      'registered', 'working', 'streaming', 'available', 'alive'
    ].includes(normalized)) return true;
    if ([
      'disconnect', 'disconnected', 'abnormal', 'inactive', 'error', 'fault',
      'failed', 'unregistered', 'videolost', 'nosignal', 'unavailable', 'dead'
    ].includes(normalized)) return false;
  }
  return null;
}

function proxyPhysicalChannel(block: Record<string, unknown>, streamingProxyChannelId: string | null): number {
  const raw = numberValue(
    block.videoInputChannelID
    ?? block.inputProxyChannelID
    ?? block.proxyChannelID
    ?? block.id
    ?? firstScalar(block, ['videoInputChannelID', 'inputProxyChannelID', 'proxyChannelID', 'id'])
  );
  if (raw != null && raw > 0) return physicalChannelFromStreamId(String(raw));
  if (streamingProxyChannelId) return physicalChannelFromStreamId(streamingProxyChannelId);
  return 0;
}

function parseProxyStatus(xml: string): ProxyStatus[] {
  const parsed = xmlParser.parse(xml);
  const blocks = [
    ...findObjects(parsed, 'InputProxyChannelStatus'),
    ...findObjects(parsed, 'InputProxyChannel')
  ];
  return blocks.map((block) => {
    const streamingProxyChannelId = scalar(
      block.streamingProxyChannelId
      ?? block.streamingProxyChannelID
      ?? firstScalar(block, ['streamingProxyChannelId', 'streamingProxyChannelID'])
    );
    const physicalChannel = proxyPhysicalChannel(block, streamingProxyChannelId);
    return {
      physicalChannel,
      name: scalar(block.name ?? block.channelName) ?? firstScalar(block, ['name', 'channelName']),
      online: statusValue(
        block.online,
        block.status,
        block.connectionStatus,
        block.connectStatus,
        block.registerStatus,
        block.channelStatus,
        block.workingStatus,
        firstScalar(block, [
          'online', 'status', 'connectionStatus', 'connectStatus',
          'registerStatus', 'channelStatus', 'workingStatus'
        ])
      ),
      enabled: boolValue(block.enabled ?? block.enable ?? firstScalar(block, ['enabled', 'enable'])),
      streamingProxyChannelId
    };
  }).filter((item) => item.physicalChannel > 0);
}

function mergeProxyStatuses(statuses: ProxyStatus[]): ProxyStatus[] {
  const merged = new Map<number, ProxyStatus>();
  for (const status of statuses) {
    const current = merged.get(status.physicalChannel);
    if (!current) {
      merged.set(status.physicalChannel, status);
      continue;
    }
    merged.set(status.physicalChannel, {
      physicalChannel: status.physicalChannel,
      name: current.name ?? status.name,
      online: current.online ?? status.online,
      enabled: current.enabled ?? status.enabled,
      streamingProxyChannelId: current.streamingProxyChannelId ?? status.streamingProxyChannelId
    });
  }
  return [...merged.values()];
}

function choosePrimary(streams: HikvisionStreamSettings[], override?: string): string {
  if (override && streams.some((stream) => stream.id === override)) return override;
  return streams.find((stream) => stream.stream_type === 'main')?.id || streams[0]?.id || '';
}

function uniqueTrackIds(streams: HikvisionStreamSettings[], physicalChannel: number): string[] {
  const result: string[] = [];
  const add = (value: string | number | null | undefined) => {
    const normalized = String(value || '').trim();
    if (normalized && !result.includes(normalized)) result.push(normalized);
  };
  for (const stream of streams) add(stream.id);
  add(`${physicalChannel}01`);
  add(physicalChannel);
  return result;
}

function mergeChannels(device: HikvisionDeviceConfig, streams: HikvisionStreamSettings[], statuses: ProxyStatus[]): HikvisionChannel[] {
  const streamsByPhysical = new Map<number, HikvisionStreamSettings[]>();
  for (const stream of streams) {
    const physical = stream.video_input_channel_id || physicalChannelFromStreamId(stream.id);
    if (!physical) continue;
    const list = streamsByPhysical.get(physical) || [];
    list.push(stream);
    streamsByPhysical.set(physical, list);
  }

  for (const status of statuses) {
    if (!streamsByPhysical.has(status.physicalChannel)) {
      const id = status.streamingProxyChannelId || `${status.physicalChannel}01`;
      streamsByPhysical.set(status.physicalChannel, [{
        id,
        stream_type: streamType(id),
        enabled: status.enabled,
        name: status.name,
        video_input_channel_id: status.physicalChannel,
        video_codec: null,
        width: null,
        height: null,
        frame_rate: null,
        bitrate_kbps: null,
        bitrate_mode: null,
        gop: null,
        audio_codec: null,
        raw: {}
      }]);
    }
  }

  return [...streamsByPhysical.entries()]
    .sort(([left], [right]) => left - right)
    .map(([physicalChannel, channelStreams]) => {
      channelStreams.sort((a, b) => Number(a.id) - Number(b.id));
      const status = statuses.find((item) => item.physicalChannel === physicalChannel);
      const id = `${device.id}:${physicalChannel}`;
      const override = device.channel_overrides?.[id] || device.channel_overrides?.[String(physicalChannel)] || {};
      return {
        id,
        device_id: device.id,
        physical_channel: physicalChannel,
        name: status?.name || channelStreams.find((item) => item.name)?.name || `${device.name} channel ${physicalChannel}`,
        online: status?.online ?? null,
        enabled: override.enabled ?? status?.enabled ?? true,
        primary_stream_id: choosePrimary(channelStreams, override.primary_stream_id),
        archive_track_ids: uniqueTrackIds(channelStreams, physicalChannel),
        archive_storage: override.archive_storage || device.archive_storage,
        retention_days: override.retention_days || device.retention_days,
        streams: channelStreams,
        discovered_at: new Date().toISOString()
      } satisfies HikvisionChannel;
    })
    .filter((channel) => Boolean(channel.primary_stream_id));
}

async function optionalGet(client: IsapiClient, path: string): Promise<string | null> {
  try {
    return await client.get(path);
  } catch {
    return null;
  }
}

async function enrichStreamSettings(
  client: IsapiClient,
  listedStreams: HikvisionStreamSettings[]
): Promise<HikvisionStreamSettings[]> {
  if (!listedStreams.length) return listedStreams;
  const results: HikvisionStreamSettings[] = new Array(listedStreams.length);
  let cursor = 0;
  const worker = async () => {
    for (;;) {
      const index = cursor;
      cursor += 1;
      const listed = listedStreams[index];
      if (!listed) return;
      try {
        const xml = await client.get(`/ISAPI/Streaming/channels/${encodeURIComponent(listed.id)}`);
        const parsed = xmlParser.parse(xml);
        const block = findObjects(parsed, 'StreamingChannel')[0] || objectValue(parsed.StreamingChannel);
        results[index] = parseStream(block) || listed;
      } catch {
        results[index] = listed;
      }
    }
  };
  const workerCount = Math.min(config.streamSettingsConcurrency, listedStreams.length);
  await Promise.all(Array.from({ length: workerCount }, () => worker()));
  return results;
}

export interface DiscoveryResult {
  device_info: Record<string, unknown>;
  capabilities: Record<string, unknown>;
  channels: HikvisionChannel[];
}

export async function discoverHikvisionDevice(device: HikvisionDeviceConfig): Promise<DiscoveryResult> {
  const client = new IsapiClient(device);
  const [deviceInfoXml, capabilitiesXml, proxyStatusXml, proxyChannelsXml, streamingXml] = await Promise.all([
    optionalGet(client, '/ISAPI/System/deviceInfo'),
    optionalGet(client, '/ISAPI/System/capabilities'),
    optionalGet(client, '/ISAPI/ContentMgmt/InputProxy/channels/status'),
    optionalGet(client, '/ISAPI/ContentMgmt/InputProxy/channels'),
    optionalGet(client, '/ISAPI/Streaming/channels')
  ]);

  if (!streamingXml && !proxyStatusXml && !proxyChannelsXml) {
    throw new Error('Device returned no ISAPI channel list');
  }

  const listedStreams = streamingXml ? parseStreamingChannels(streamingXml) : [];
  const streams = await enrichStreamSettings(client, listedStreams);
  const statuses = mergeProxyStatuses([
    ...(proxyStatusXml ? parseProxyStatus(proxyStatusXml) : []),
    ...(proxyChannelsXml ? parseProxyStatus(proxyChannelsXml) : [])
  ]);
  const channels = mergeChannels(device, streams, statuses);
  if (!channels.length) throw new Error('No live channels were discovered on the Hikvision device');

  return {
    device_info: deviceInfoXml ? objectValue(xmlParser.parse(deviceInfoXml)) : {},
    capabilities: capabilitiesXml ? objectValue(xmlParser.parse(capabilitiesXml)) : {},
    channels
  };
}

export async function fetchStreamingChannelSettings(device: HikvisionDeviceConfig, streamId: string): Promise<HikvisionStreamSettings> {
  const client = new IsapiClient(device);
  const xml = await client.get(`/ISAPI/Streaming/channels/${encodeURIComponent(streamId)}`);
  const parsed = xmlParser.parse(xml);
  const block = findObjects(parsed, 'StreamingChannel')[0] || objectValue(parsed.StreamingChannel);
  const settings = parseStream(block);
  if (!settings) throw new Error(`Invalid streaming settings response for ${streamId}`);
  return settings;
}

export function deviceIdentitySummary(deviceInfo: Record<string, unknown>): Record<string, string | null> {
  return {
    device_name: firstScalar(deviceInfo, ['deviceName']),
    model: firstScalar(deviceInfo, ['model']),
    serial_number: firstScalar(deviceInfo, ['serialNumber']),
    firmware_version: firstScalar(deviceInfo, ['firmwareVersion']),
    firmware_released_date: firstScalar(deviceInfo, ['firmwareReleasedDate']),
    device_type: firstScalar(deviceInfo, ['deviceType'])
  };
}
