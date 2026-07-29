import path from 'node:path';

function firstEnv(...names: string[]): string {
  for (const name of names) {
    const value = String(process.env[name] || '').trim();
    if (value) return value;
  }
  return '';
}

function requiredAny(...names: string[]): string {
  const value = firstEnv(...names);
  if (!value) throw new Error(`${names.join(' or ')} is required`);
  return value;
}

function numberEnv(name: string, fallback: number, min = 0): number {
  const raw = process.env[name];
  const value = raw == null || raw === '' ? fallback : Number(raw);
  if (!Number.isFinite(value) || value < min) throw new Error(`${name} must be a number >= ${min}`);
  return value;
}

function boolEnv(name: string, fallback: boolean): boolean {
  const raw = process.env[name];
  if (raw == null || raw === '') return fallback;
  return ['1', 'true', 'yes', 'on'].includes(raw.toLowerCase());
}

const root = process.env.HIK_NODE_ROOT || '/var/lib/newdomofon-video-hik';
const nodeToken = requiredAny('DVR_NODE_TOKEN', 'HIK_NODE_TOKEN');
const mediaSecret = requiredAny('DVR_NODE_MEDIA_SECRET', 'HIK_NODE_MEDIA_SECRET');

export const config = {
  env: process.env.NODE_ENV || 'production',
  port: numberEnv('HIK_NODE_PORT', 3020, 1),
  host: process.env.HIK_NODE_HOST || '0.0.0.0',

  // The same operator-defined credentials are entered in the master UI.
  masterUrl: firstEnv('DVR_MASTER_URL', 'HIK_MASTER_URL').replace(/\/+$/, ''),
  nodeId: firstEnv('DVR_NODE_ID', 'HIK_NODE_ID'),
  nodeToken,
  agentToken: nodeToken,
  mediaSecret,
  publicBaseUrl: firstEnv('DVR_NODE_PUBLIC_BASE_URL', 'HIK_NODE_PUBLIC_BASE_URL').replace(/\/+$/, ''),
  internalUrl: firstEnv('DVR_NODE_INTERNAL_URL', 'HIK_NODE_INTERNAL_URL').replace(/\/+$/, ''),
  masterRequestTimeoutMs: numberEnv('HIK_MASTER_REQUEST_TIMEOUT_MS', 10_000, 1000),
  masterPollSeconds: numberEnv('HIK_MASTER_POLL_SECONDS', 20, 5),
  heartbeatSeconds: numberEnv('HIK_HEARTBEAT_SECONDS', 20, 5),

  stateKey: requiredAny('HIK_NODE_STATE_KEY'),
  root,
  stateFile: process.env.HIK_NODE_STATE_FILE || path.join(root, 'state.enc.json'),
  archiveRoot: process.env.HIK_NODE_ARCHIVE_ROOT || path.join(root, 'archive'),
  liveRoot: process.env.HIK_NODE_LIVE_ROOT || path.join(root, 'live'),
  tempRoot: process.env.HIK_NODE_TEMP_ROOT || path.join(root, 'tmp'),
  ffmpegPath: process.env.FFMPEG_PATH || '/usr/bin/ffmpeg',
  ffprobePath: process.env.FFPROBE_PATH || '/usr/bin/ffprobe',
  requestTimeoutMs: numberEnv('HIK_ISAPI_TIMEOUT_MS', 10_000, 1000),
  syncIntervalSeconds: numberEnv('HIK_CHANNEL_SYNC_SECONDS', 300, 10),
  streamSettingsConcurrency: numberEnv('HIK_STREAM_SETTINGS_CONCURRENCY', 4, 1),
  segmentSeconds: numberEnv('HIK_SEGMENT_SECONDS', 4, 1),
  liveWindow: numberEnv('HIK_LIVE_WINDOW', 8, 2),
  archiveSearchPageSize: numberEnv('HIK_ARCHIVE_SEARCH_PAGE_SIZE', 64, 1),
  archiveSearchMaxPages: numberEnv('HIK_ARCHIVE_SEARCH_MAX_PAGES', 80, 1),
  deviceArchiveMaxSeconds: numberEnv('HIK_DEVICE_ARCHIVE_MAX_SECONDS', 3600, 1),
  deviceArchiveSessionSeconds: numberEnv('HIK_DEVICE_ARCHIVE_SESSION_SECONDS', 300, 10),
  deviceArchiveSessionKeepMs: numberEnv('HIK_DEVICE_ARCHIVE_SESSION_KEEP_MS', 900_000, 60_000),
  mediaTokenMaxSeconds: numberEnv('HIK_MEDIA_TOKEN_MAX_SECONDS', 3600, 30),
  transcodeH265: boolEnv('HIK_TRANSCODE_H265', true),
  rtspTransport: process.env.HIK_RTSP_TRANSPORT || 'tcp',
  logLevel: process.env.HIK_FFMPEG_LOGLEVEL || 'warning'
} as const;

export function isMasterPairingConfigured(): boolean {
  return Boolean(config.masterUrl && config.nodeId && config.nodeToken && config.publicBaseUrl && config.internalUrl);
}
