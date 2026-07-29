import type { HikvisionChannel, HikvisionDeviceConfig } from '../types.js';

function auth(device: HikvisionDeviceConfig): string {
  return device.username
    ? `${encodeURIComponent(device.username)}:${encodeURIComponent(device.password)}@`
    : '';
}

export function liveRtspCandidates(device: HikvisionDeviceConfig, channel: HikvisionChannel): string[] {
  const base = `rtsp://${auth(device)}${device.host}:${device.rtsp_port}`;
  const streamId = encodeURIComponent(channel.primary_stream_id);
  return [
    `${base}/Streaming/channels/${streamId}`,
    `${base}/ISAPI/Streaming/channels/${streamId}`
  ];
}

export function playbackRtspFallback(
  device: HikvisionDeviceConfig,
  trackId: string,
  start: Date,
  end: Date
): string {
  const compact = (date: Date) => date.toISOString().replace(/[-:]/g, '').replace(/\.\d{3}Z$/, 'Z');
  return `rtsp://${auth(device)}${device.host}:${device.rtsp_port}/Streaming/tracks/${encodeURIComponent(trackId)}`
    + `?starttime=${compact(start)}&endtime=${compact(end)}`;
}

export function injectRtspCredentials(raw: string, device: HikvisionDeviceConfig): string {
  try {
    const url = new URL(raw);
    if (url.protocol !== 'rtsp:' || url.username || !device.username) return raw;
    url.username = device.username;
    url.password = device.password;
    if (['0.0.0.0', '127.0.0.1', 'localhost'].includes(url.hostname.toLowerCase())) url.hostname = device.host;
    if (!url.port) url.port = String(device.rtsp_port);
    return url.toString();
  } catch {
    return raw;
  }
}

export function redactRtsp(raw: string): string {
  return raw.replace(/rtsp:\/\/[^\s/@]+(?::[^\s/@]*)?@/gi, 'rtsp://***:***@');
}
