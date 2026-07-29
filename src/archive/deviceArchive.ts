import crypto from 'node:crypto';
import { spawn } from 'node:child_process';
import type { Response } from 'express';
import { config } from '../config.js';
import type { DeviceArchiveItem, HikvisionChannel, HikvisionDeviceConfig } from '../types.js';
import { IsapiClient } from '../isapi/client.js';
import { findObjects, firstScalar, xmlParser } from '../isapi/xml.js';
import { injectRtspCredentials, playbackRtspFallback, redactRtsp } from '../media/rtsp.js';

function formatIsapiTime(date: Date): string {
  return date.toISOString().replace(/\.\d{3}Z$/, 'Z');
}

function requestXml(searchId: string, trackId: string, start: Date, end: Date, position: number): string {
  return `<?xml version="1.0" encoding="UTF-8"?>
<CMSearchDescription>
  <searchID>${searchId}</searchID>
  <trackList><trackID>${trackId}</trackID></trackList>
  <timeSpanList><timeSpan><startTime>${formatIsapiTime(start)}</startTime><endTime>${formatIsapiTime(end)}</endTime></timeSpan></timeSpanList>
  <maxResults>${config.archiveSearchPageSize}</maxResults>
  <searchResultPosition>${position}</searchResultPosition>
  <metadataList><metadataDescriptor>//recordType.meta.std-cgi.com</metadataDescriptor></metadataList>
</CMSearchDescription>`;
}

function validDate(value: string | null): Date | null {
  if (!value) return null;
  const parsed = new Date(value);
  return Number.isFinite(parsed.getTime()) ? parsed : null;
}

function normalizePlaybackUri(raw: string, device: HikvisionDeviceConfig, trackId: string, start: Date, end: Date): string {
  if (!raw) return playbackRtspFallback(device, trackId, start, end);
  try {
    const url = new URL(raw);
    if (url.protocol !== 'rtsp:') return playbackRtspFallback(device, trackId, start, end);
    if (['0.0.0.0', '127.0.0.1', 'localhost'].includes(url.hostname.toLowerCase())) url.hostname = device.host;
    if (!url.port) url.port = String(device.rtsp_port);
    if (!url.searchParams.has('starttime')) url.searchParams.set('starttime', start.toISOString().replace(/[-:]/g, '').replace(/\.\d{3}Z$/, 'Z'));
    if (!url.searchParams.has('endtime')) url.searchParams.set('endtime', end.toISOString().replace(/[-:]/g, '').replace(/\.\d{3}Z$/, 'Z'));
    return injectRtspCredentials(url.toString(), device);
  } catch {
    return playbackRtspFallback(device, trackId, start, end);
  }
}

function parseSearch(xml: string, device: HikvisionDeviceConfig, fallbackTrackId: string): DeviceArchiveItem[] {
  const parsed = xmlParser.parse(xml);
  const blocks = findObjects(parsed, 'searchMatchItem');
  return blocks.map((block) => {
    const start = validDate(firstScalar(block, ['startTime']));
    const end = validDate(firstScalar(block, ['endTime']));
    const rawUri = firstScalar(block, ['playbackURI']);
    const trackId = firstScalar(block, ['trackID']) || fallbackTrackId;
    if (!start || !end || end <= start || !rawUri) return null;
    return {
      start: start.toISOString(),
      end: end.toISOString(),
      source: 'device',
      playback_uri: normalizePlaybackUri(rawUri, device, trackId, start, end),
      track_id: trackId
    } satisfies DeviceArchiveItem;
  }).filter((item): item is DeviceArchiveItem => Boolean(item));
}

async function searchTrack(
  client: IsapiClient,
  device: HikvisionDeviceConfig,
  trackId: string,
  start: Date,
  end: Date
): Promise<DeviceArchiveItem[]> {
  const searchId = crypto.randomUUID();
  const result: DeviceArchiveItem[] = [];
  for (let page = 0; page < config.archiveSearchMaxPages; page += 1) {
    const position = page * config.archiveSearchPageSize;
    const body = requestXml(searchId, trackId, start, end, position);
    let xml: string;
    try {
      xml = await client.post('/ISAPI/ContentMgmt/search', body);
    } catch {
      xml = await client.post('/ISAPI/ContentMgmt/search/', body);
    }
    const items = parseSearch(xml, device, trackId);
    result.push(...items);
    if (items.length < config.archiveSearchPageSize) break;
  }
  return result;
}

export async function searchDeviceArchive(
  device: HikvisionDeviceConfig,
  channel: HikvisionChannel,
  start: Date,
  end: Date
): Promise<DeviceArchiveItem[]> {
  const client = new IsapiClient(device);
  const items: DeviceArchiveItem[] = [];
  const errors: string[] = [];
  for (const trackId of channel.archive_track_ids) {
    try {
      const found = await searchTrack(client, device, trackId, start, end);
      items.push(...found);
      if (found.length) break;
    } catch (error) {
      errors.push(error instanceof Error ? error.message : String(error));
    }
  }
  const unique = new Map<string, DeviceArchiveItem>();
  for (const item of items) unique.set(`${item.track_id}|${item.start}|${item.end}|${item.playback_uri}`, item);
  const sorted = [...unique.values()].sort((a, b) => new Date(a.start).getTime() - new Date(b.start).getTime());
  if (!sorted.length && errors.length) console.warn(`[device-archive:${channel.id}] search errors: ${errors.join(' | ')}`);
  return sorted;
}

export async function devicePlaybackCandidates(
  device: HikvisionDeviceConfig,
  channel: HikvisionChannel,
  start: Date,
  end: Date
): Promise<string[]> {
  const items = await searchDeviceArchive(device, channel, start, end);
  const candidates = items
    .filter((item) => new Date(item.end) >= start && new Date(item.start) <= end)
    .map((item) => item.playback_uri);
  for (const trackId of channel.archive_track_ids) candidates.push(playbackRtspFallback(device, trackId, start, end));
  return [...new Set(candidates)];
}

export async function streamDeviceArchiveMp4(
  device: HikvisionDeviceConfig,
  channel: HikvisionChannel,
  start: Date,
  end: Date,
  res: Response
): Promise<void> {
  const duration = Math.min(config.deviceArchiveMaxSeconds, Math.max(1, Math.ceil((end.getTime() - start.getTime()) / 1000)));
  const candidates = await devicePlaybackCandidates(device, channel, start, end);
  if (!candidates.length) {
    res.status(404).json({ error: 'No device archive playback candidate' });
    return;
  }

  const tryCandidate = (index: number): void => {
    const input = candidates[index];
    if (!input) {
      if (!res.headersSent) res.status(502).json({ error: 'All Hikvision archive playback candidates failed' });
      else res.end();
      return;
    }
    console.log(`[device-archive:${channel.id}] export candidate ${index + 1}/${candidates.length}: ${redactRtsp(input)}`);
    const child = spawn(config.ffmpegPath, [
      '-hide_banner',
      '-loglevel', config.logLevel,
      '-rtsp_transport', config.rtspTransport,
      '-timeout', '15000000',
      '-i', input,
      '-t', String(duration),
      '-map', '0:v:0',
      '-map', '0:a?',
      '-c:v', 'copy',
      '-c:a', 'aac',
      '-movflags', 'frag_keyframe+empty_moov+default_base_moof',
      '-f', 'mp4',
      'pipe:1'
    ], { stdio: ['ignore', 'pipe', 'pipe'] });
    let total = 0;
    let stderr = '';
    child.stdout.on('data', (chunk: Buffer) => {
      total += chunk.length;
      if (!res.headersSent) {
        res.status(200);
        res.setHeader('Content-Type', 'video/mp4');
        res.setHeader('Cache-Control', 'no-store');
      }
      res.write(chunk);
    });
    child.stderr.on('data', (chunk) => { stderr = `${stderr}\n${String(chunk)}`.slice(-4000); });
    child.once('exit', (code) => {
      if (total === 0 && code !== 0 && index + 1 < candidates.length) {
        tryCandidate(index + 1);
        return;
      }
      if (total === 0 && !res.headersSent) res.status(502).json({ error: stderr || `ffmpeg exited ${code}` });
      else if (!res.writableEnded) res.end();
    });
    res.once('close', () => child.kill('SIGTERM'));
  };
  tryCandidate(0);
}
