export type ArchiveStorage = 'node' | 'device';
export type DeviceScheme = 'http' | 'https';
export type StreamType = 'main' | 'sub' | 'third' | 'other';

export interface HikvisionDeviceConfig {
  id: string;
  name: string;
  host: string;
  scheme: DeviceScheme;
  isapi_port: number;
  rtsp_port: number;
  username: string;
  password: string;
  archive_storage: ArchiveStorage;
  retention_days: number;
  enabled: boolean;
  reject_unauthorized_tls: boolean;
  channel_overrides?: Record<string, Partial<Pick<HikvisionChannel, 'enabled' | 'archive_storage' | 'retention_days' | 'primary_stream_id'>>>;
}

export interface HikvisionStreamSettings {
  id: string;
  stream_type: StreamType;
  enabled: boolean | null;
  name: string | null;
  video_input_channel_id: number | null;
  video_codec: string | null;
  width: number | null;
  height: number | null;
  frame_rate: number | null;
  bitrate_kbps: number | null;
  bitrate_mode: string | null;
  gop: number | null;
  audio_codec: string | null;
  raw: Record<string, unknown>;
}

export interface HikvisionChannel {
  id: string;
  device_id: string;
  physical_channel: number;
  name: string;
  online: boolean | null;
  enabled: boolean;
  primary_stream_id: string;
  archive_track_ids: string[];
  archive_storage: ArchiveStorage;
  retention_days: number;
  streams: HikvisionStreamSettings[];
  discovered_at: string;
}

export interface HikvisionDeviceSnapshot {
  config: HikvisionDeviceConfig;
  device_info: Record<string, unknown>;
  capabilities: Record<string, unknown>;
  channels: HikvisionChannel[];
  last_sync_at: string | null;
  last_sync_error: string | null;
}

export interface PersistedState {
  version: 1;
  devices: HikvisionDeviceSnapshot[];
}

export interface ArchiveRange {
  start: string;
  end: string;
  source: ArchiveStorage;
}

export interface DeviceArchiveItem extends ArchiveRange {
  source: 'device';
  playback_uri: string;
  track_id: string;
}

export interface RecorderStatus {
  channel_id: string;
  running: boolean;
  pid: number | null;
  mode: 'node-archive' | 'live-only';
  source_candidate: string | null;
  restarts: number;
  started_at: string | null;
  last_error: string | null;
}
