#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if new in text:
        return text
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one source block, found {count}")
    return text.replace(old, new, 1)


def patch_config(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    text = replace_once(
        text,
        "  segmentSeconds: numberEnv('HIK_SEGMENT_SECONDS', 4, 1),\n  liveWindow: numberEnv('HIK_LIVE_WINDOW', 8, 2),",
        "  segmentSeconds: numberEnv('HIK_SEGMENT_SECONDS', 1, 1),\n  liveWindow: numberEnv('HIK_LIVE_WINDOW', 6, 2),\n  liveStreamPolicy: (firstEnv('HIK_LIVE_STREAM_POLICY') || 'auto').toLowerCase(),\n  x264Preset: firstEnv('HIK_X264_PRESET') || 'ultrafast',\n  archiveRangeCacheMs: numberEnv('HIK_ARCHIVE_RANGE_CACHE_MS', 120_000, 0),\n  archiveFmp4: boolEnv('HIK_ARCHIVE_FMP4', true),",
        "config performance settings",
    )
    path.write_text(text, encoding="utf-8")


def patch_rtsp(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    text = replace_once(
        text,
        "export function liveRtspCandidates(device: HikvisionDeviceConfig, channel: HikvisionChannel): string[] {\n  const base = `rtsp://${auth(device)}${device.host}:${device.rtsp_port}`;\n  const streamId = encodeURIComponent(channel.primary_stream_id);",
        "export function liveRtspCandidates(\n  device: HikvisionDeviceConfig,\n  channel: HikvisionChannel,\n  streamIdOverride?: string\n): string[] {\n  const base = `rtsp://${auth(device)}${device.host}:${device.rtsp_port}`;\n  const streamId = encodeURIComponent(streamIdOverride || channel.primary_stream_id);",
        "live RTSP override",
    )
    path.write_text(text, encoding="utf-8")


def patch_recorder(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    text = replace_once(text, "import { config } from '../config.js';\n", "import { config } from '../config.js';\nimport { selectLiveStreamId } from './performance.js';\n", "recorder performance import")
    text = replace_once(text, "  channel: HikvisionChannel;\n  process: ChildProcess | null;", "  channel: HikvisionChannel;\n  liveStreamId: string;\n  process: ChildProcess | null;", "runtime live stream id")
    text = replace_once(
        text,
        "function channelCodec(channel: HikvisionChannel): string {\n  return channel.streams.find((stream) => stream.id === channel.primary_stream_id)?.video_codec || '';\n}\n\nfunction recorderKey(device: HikvisionDeviceConfig, channel: HikvisionChannel): string {\n  return `${device.id}|${channel.id}|${channel.primary_stream_id}|${channel.archive_storage}|${channel.enabled}`;\n}\n\nfunction hlsArgs(device: HikvisionDeviceConfig, channel: HikvisionChannel, input: string): { cwd: string; args: string[] } {\n  const nodeArchive = channel.archive_storage === 'node';\n  const cwd = nodeArchive ? archiveDir(channel.id) : liveDir(channel.id);\n  const codec = channelCodec(channel);\n  const transcodeVideo = config.transcodeH265 && /H\\.?265|HEVC/i.test(codec);",
        "function recorderKey(device: HikvisionDeviceConfig, channel: HikvisionChannel, liveStreamId: string): string {\n  return `${device.id}|${channel.id}|${liveStreamId}|${channel.archive_storage}|${channel.enabled}`;\n}\n\nfunction hlsArgs(device: HikvisionDeviceConfig, channel: HikvisionChannel, liveStreamId: string, input: string): { cwd: string; args: string[] } {\n  const nodeArchive = channel.archive_storage === 'node';\n  const cwd = nodeArchive ? archiveDir(channel.id) : liveDir(channel.id);\n  const selectedStream = channel.streams.find((stream) => stream.id === liveStreamId);\n  const codec = selectedStream?.video_codec || '';\n  const fps = selectedStream?.frame_rate || 25;\n  const keyframeFrames = Math.max(12, Math.round(fps * config.segmentSeconds));\n  const transcodeVideo = config.transcodeH265 && /H\\.?265|HEVC/i.test(codec);",
        "recorder stream selection",
    )
    text = replace_once(text, "      '-preset', 'veryfast',\n      '-tune', 'zerolatency',\n      '-g', String(Math.max(25, Math.round((channel.streams[0]?.frame_rate || 25) * 2))),\n      '-sc_threshold', '0'", "      '-preset', config.x264Preset,\n      '-tune', 'zerolatency',\n      '-g', String(keyframeFrames),\n      '-keyint_min', String(keyframeFrames),\n      '-sc_threshold', '0',\n      '-force_key_frames', `expr:gte(t,n_forced*${config.segmentSeconds})`", "recorder x264 startup")
    text = replace_once(text, "    ...(nodeArchive ? ['-strftime', '1', '-strftime_mkdir', '1'] : ['-hls_delete_threshold', '2']),", "    ...(nodeArchive ? ['-strftime', '1', '-strftime_mkdir', '1'] : ['-hls_delete_threshold', String(Math.max(4, config.liveWindow))]),", "live delete threshold")
    text = replace_once(text, "    const wanted = new Map<string, { device: HikvisionDeviceConfig; channel: HikvisionChannel }>();", "    const wanted = new Map<string, { device: HikvisionDeviceConfig; channel: HikvisionChannel; liveStreamId: string }>();", "wanted map type")
    text = replace_once(text, "        wanted.set(channel.id, { device: item.config, channel });", "        const liveStreamId = selectLiveStreamId(channel, config.liveStreamPolicy);\n        wanted.set(channel.id, { device: item.config, channel, liveStreamId });", "wanted stream selection")
    text = replace_once(text, "      if (!next || runtime.key !== recorderKey(next.device, next.channel)) {", "      if (!next || runtime.key !== recorderKey(next.device, next.channel, next.liveStreamId)) {", "recorder reconcile key")
    text = replace_once(text, "      if (!this.recorders.has(channelId)) await this.start(next.device, next.channel);", "      if (!this.recorders.has(channelId)) await this.start(next.device, next.channel, next.liveStreamId);", "recorder start selected stream")
    text = replace_once(text, "  private async start(device: HikvisionDeviceConfig, channel: HikvisionChannel): Promise<void> {\n    const runtime: RuntimeRecorder = {\n      key: recorderKey(device, channel),\n      device,\n      channel,", "  private async start(device: HikvisionDeviceConfig, channel: HikvisionChannel, liveStreamId: string): Promise<void> {\n    const runtime: RuntimeRecorder = {\n      key: recorderKey(device, channel, liveStreamId),\n      device,\n      channel,\n      liveStreamId,", "recorder runtime selected stream")
    text = replace_once(text, "    const candidates = liveRtspCandidates(runtime.device, runtime.channel);\n    const input = candidates[runtime.candidateIndex % candidates.length]!;\n    const { cwd, args } = hlsArgs(runtime.device, runtime.channel, input);", "    const candidates = liveRtspCandidates(runtime.device, runtime.channel, runtime.liveStreamId);\n    const input = candidates[runtime.candidateIndex % candidates.length]!;\n    const { cwd, args } = hlsArgs(runtime.device, runtime.channel, runtime.liveStreamId, input);", "recorder candidates selected stream")
    text = replace_once(text, "      console.log(`[recorder:${runtime.channel.id}] started pid=${child.pid} source=${redactRtsp(input)} archive=${runtime.channel.archive_storage}`);", "      console.log(`[recorder:${runtime.channel.id}] started pid=${child.pid} stream=${runtime.liveStreamId} source=${redactRtsp(input)} archive=${runtime.channel.archive_storage}`);", "recorder startup diagnostics")
    text = replace_once(text, "    const candidate = liveRtspCandidates(runtime.device, runtime.channel)[runtime.candidateIndex] || null;", "    const candidate = liveRtspCandidates(runtime.device, runtime.channel, runtime.liveStreamId)[runtime.candidateIndex] || null;", "recorder status selected stream")
    path.write_text(text, encoding="utf-8")


def patch_archive_search(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    if "interface ArchiveSearchCacheEntry" not in text:
        insertion = '''interface ArchiveSearchCacheEntry {\n  startMs: number;\n  endMs: number;\n  expiresAt: number;\n  items: DeviceArchiveItem[];\n}\n\nconst archiveSearchCache = new Map<string, ArchiveSearchCacheEntry[]>();\n\nfunction cachedArchiveItems(device: HikvisionDeviceConfig, channel: HikvisionChannel, start: Date, end: Date): DeviceArchiveItem[] | null {\n  if (config.archiveRangeCacheMs <= 0) return null;\n  const key = `${device.id}|${channel.id}`;\n  const now = Date.now();\n  const entries = (archiveSearchCache.get(key) || []).filter((entry) => entry.expiresAt > now);\n  archiveSearchCache.set(key, entries);\n  const match = entries.find((entry) => entry.startMs <= start.getTime() && entry.endMs >= end.getTime());\n  if (!match) return null;\n  return match.items.filter((item) => new Date(item.end) >= start && new Date(item.start) <= end).map((item) => ({ ...item }));\n}\n\nfunction rememberArchiveItems(device: HikvisionDeviceConfig, channel: HikvisionChannel, start: Date, end: Date, items: DeviceArchiveItem[]): void {\n  if (config.archiveRangeCacheMs <= 0) return;\n  const key = `${device.id}|${channel.id}`;\n  const now = Date.now();\n  const next = (archiveSearchCache.get(key) || []).filter((entry) => entry.expiresAt > now).slice(-15);\n  next.push({ startMs: start.getTime(), endMs: end.getTime(), expiresAt: now + config.archiveRangeCacheMs, items: items.map((item) => ({ ...item })) });\n  archiveSearchCache.set(key, next);\n}\n\n'''
        text = text.replace("function formatIsapiTime(date: Date): string {", insertion + "function formatIsapiTime(date: Date): string {", 1)
    text = replace_once(text, "export async function searchDeviceArchive(\n  device: HikvisionDeviceConfig,\n  channel: HikvisionChannel,\n  start: Date,\n  end: Date\n): Promise<DeviceArchiveItem[]> {\n  const client = new IsapiClient(device);", "export async function searchDeviceArchive(\n  device: HikvisionDeviceConfig,\n  channel: HikvisionChannel,\n  start: Date,\n  end: Date\n): Promise<DeviceArchiveItem[]> {\n  const cached = cachedArchiveItems(device, channel, start, end);\n  if (cached) return cached;\n  const client = new IsapiClient(device);", "archive search cache read")
    text = replace_once(text, "  if (!sorted.length && errors.length) console.warn(`[device-archive:${channel.id}] search errors: ${errors.join(' | ')}`);\n  return sorted;", "  if (!sorted.length && errors.length) console.warn(`[device-archive:${channel.id}] search errors: ${errors.join(' | ')}`);\n  rememberArchiveItems(device, channel, start, end, sorted);\n  return sorted;", "archive search cache write")
    path.write_text(text, encoding="utf-8")


def patch_archive_sessions(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    text = replace_once(text, "import { devicePlaybackCandidates } from './deviceArchive.js';\n", "import { devicePlaybackCandidates } from './deviceArchive.js';\nimport { archiveFrameRate, shouldCopyArchiveAudio, shouldCopyArchiveVideo } from '../media/performance.js';\n", "archive performance import")
    old = """      const child = spawn(config.ffmpegPath, [
        '-hide_banner',
        '-loglevel', config.logLevel,
        '-rtsp_transport', config.rtspTransport,
        '-timeout', '15000000',
        '-i', input,
        '-t', String(duration),
        '-map', '0:v:0',
        '-map', '0:a?',
        '-c:v', 'libx264',
        '-preset', 'veryfast',
        '-tune', 'zerolatency',
        '-g', '50',
        '-sc_threshold', '0',
        '-c:a', 'aac',
        '-b:a', '64k',
        '-f', 'hls',
        '-hls_time', '2',
        '-hls_list_size', '0',
        '-hls_flags', 'temp_file+program_date_time+independent_segments',
        '-hls_segment_filename', 'seg_%06d.ts',
        'index.m3u8'
      ], { cwd: session.dir, stdio: ['ignore', 'ignore', 'pipe'] });"""
    new = """      const copyVideo = shouldCopyArchiveVideo(channel);
      const copyAudio = shouldCopyArchiveAudio(channel);
      const fps = archiveFrameRate(channel);
      const keyframeFrames = Math.max(12, Math.round(fps));
      const segmentExtension = config.archiveFmp4 ? 'm4s' : 'ts';
      const args = [
        '-hide_banner', '-loglevel', config.logLevel,
        '-rtsp_transport', config.rtspTransport, '-timeout', '15000000',
        '-analyzeduration', '1000000', '-probesize', '1000000',
        '-fflags', '+genpts+discardcorrupt', '-i', input,
        '-t', String(duration), '-map', '0:v:0', '-map', '0:a?'
      ];
      if (copyVideo) args.push('-c:v', 'copy');
      else args.push('-c:v', 'libx264', '-preset', config.x264Preset, '-tune', 'zerolatency', '-g', String(keyframeFrames), '-keyint_min', String(keyframeFrames), '-sc_threshold', '0', '-force_key_frames', 'expr:gte(t,n_forced*1)');
      if (copyAudio) args.push('-c:a', 'copy');
      else args.push('-c:a', 'aac', '-b:a', '64k');
      args.push('-f', 'hls', '-hls_time', '1', '-hls_list_size', '0', '-hls_flags', 'temp_file+program_date_time+independent_segments');
      if (config.archiveFmp4) args.push('-hls_segment_type', 'fmp4', '-hls_fmp4_init_filename', 'init.mp4');
      args.push('-hls_segment_filename', `seg_%06d.${segmentExtension}`, 'index.m3u8');
      console.log(`[device-archive:${channel.id}] session=${session.id} video=${copyVideo ? 'copy' : 'h264-transcode'} audio=${copyAudio ? 'copy' : 'aac'} segment=${segmentExtension}`);
      const child = spawn(config.ffmpegPath, args, { cwd: session.dir, stdio: ['ignore', 'ignore', 'pipe'] });"""
    text = replace_once(text, old, new, "archive ffmpeg fast path")
    path.write_text(text, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-dir", default=".")
    args = parser.parse_args()
    root = Path(args.project_dir).resolve()
    patch_config(root / "src/config.ts")
    patch_rtsp(root / "src/media/rtsp.ts")
    patch_recorder(root / "src/media/recorderManager.ts")
    patch_archive_search(root / "src/archive/deviceArchive.ts")
    patch_archive_sessions(root / "src/archive/deviceArchiveSessions.ts")
    print("Hikvision fast media startup prepared")


if __name__ == "__main__":
    main()
