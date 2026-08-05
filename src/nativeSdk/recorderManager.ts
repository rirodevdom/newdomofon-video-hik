import { spawn, type ChildProcess } from 'node:child_process';
import fs from 'node:fs/promises';
import path from 'node:path';
import { config } from '../config.js';
import type { HikvisionChannel, HikvisionDeviceConfig, RecorderStatus } from '../types.js';
import { archiveDir, liveDir } from '../media/paths.js';
import { spawnNativeStream } from './client.js';

interface RuntimeRecorder {
  key: string;
  device: HikvisionDeviceConfig;
  channel: HikvisionChannel;
  worker: ChildProcess | null;
  ffmpeg: ChildProcess | null;
  restarts: number;
  startedAt: Date | null;
  lastError: string | null;
  restartTimer: NodeJS.Timeout | null;
  stopping: boolean;
  generation: number;
}

function recorderKey(device: HikvisionDeviceConfig, channel: HikvisionChannel): string {
  return `${device.id}|${channel.id}|${channel.sdk_channel}|${channel.primary_stream_id}|${channel.archive_storage}|${channel.enabled}|${channel.online}`;
}

function streamType(channel: HikvisionChannel): number {
  const selected = channel.streams.find((item) => item.id === channel.primary_stream_id);
  return selected?.stream_type === 'sub' ? 1 : selected?.stream_type === 'third' ? 2 : 0;
}

function hlsArgs(channel: HikvisionChannel): { cwd: string; args: string[] } {
  const nodeArchive = channel.archive_storage === 'node';
  const cwd = nodeArchive ? archiveDir(channel.id) : liveDir(channel.id);
  const codec = channel.streams.find((item) => item.id === channel.primary_stream_id)?.video_codec || '';
  const transcodeVideo = config.transcodeH265 && /H\.?265|HEVC/i.test(codec);
  const segmentPattern = nodeArchive
    ? '%Y-%m-%d/%H/%Y%m%d_%H%M%S.ts'
    : 'segments/seg_%09d.ts';
  const hlsFlags = nodeArchive
    ? 'temp_file+program_date_time+omit_endlist+independent_segments'
    : 'temp_file+program_date_time+omit_endlist+independent_segments+delete_segments';
  const args = [
    '-hide_banner', '-loglevel', config.logLevel,
    '-fflags', '+genpts+discardcorrupt',
    '-probesize', '2097152',
    '-analyzeduration', '2000000',
    '-f', 'mpeg',
    '-i', 'pipe:0',
    '-map', '0:v:0',
    '-map', '0:a?'
  ];
  if (transcodeVideo) {
    args.push('-c:v', 'libx264', '-preset', 'veryfast', '-tune', 'zerolatency', '-g', '50', '-sc_threshold', '0');
  } else {
    args.push('-c:v', 'copy');
  }
  args.push(
    '-c:a', 'aac', '-b:a', '64k', '-ac', '1', '-ar', '44100',
    '-f', 'hls',
    '-hls_time', String(config.segmentSeconds),
    '-hls_list_size', String(config.liveWindow),
    '-hls_flags', hlsFlags,
    ...(nodeArchive ? ['-strftime', '1', '-strftime_mkdir', '1'] : ['-hls_delete_threshold', '2']),
    '-hls_segment_filename', segmentPattern,
    'live.m3u8'
  );
  return { cwd, args };
}

export class NativeSdkRecorderManager {
  private readonly recorders = new Map<string, RuntimeRecorder>();

  async reconcile(devices: Array<{ config: HikvisionDeviceConfig; channels: HikvisionChannel[] }>): Promise<void> {
    const wanted = new Map<string, { device: HikvisionDeviceConfig; channel: HikvisionChannel }>();
    for (const item of devices) {
      if (!item.config.enabled) continue;
      for (const channel of item.channels) {
        // An offline digital channel is still kept in discovery/master state,
        // but must not consume an NVR private-stream resource until it comes online.
        if (channel.enabled && channel.online !== false) wanted.set(channel.id, { device: item.config, channel });
      }
    }
    for (const [channelId, runtime] of this.recorders) {
      const next = wanted.get(channelId);
      if (!next || runtime.key !== recorderKey(next.device, next.channel)) this.stop(channelId, next ? 'configuration changed' : 'channel offline, removed or disabled');
    }
    for (const [channelId, next] of wanted) {
      if (!this.recorders.has(channelId)) await this.start(next.device, next.channel);
    }
  }

  private async start(device: HikvisionDeviceConfig, channel: HikvisionChannel): Promise<void> {
    const runtime: RuntimeRecorder = {
      key: recorderKey(device, channel), device, channel,
      worker: null, ffmpeg: null, restarts: 0, startedAt: null,
      lastError: null, restartTimer: null, stopping: false, generation: 0
    };
    this.recorders.set(channel.id, runtime);
    await this.spawn(runtime);
  }

  private async spawn(runtime: RuntimeRecorder): Promise<void> {
    const generation = ++runtime.generation;
    const { cwd, args } = hlsArgs(runtime.channel);
    await fs.mkdir(cwd, { recursive: true, mode: 0o750 });
    if (runtime.channel.archive_storage === 'device') await fs.mkdir(path.join(cwd, 'segments'), { recursive: true, mode: 0o750 });
    runtime.stopping = false;
    runtime.lastError = null;

    const ffmpeg = spawn(config.ffmpegPath, args, { cwd, stdio: ['pipe', 'ignore', 'pipe'], env: { ...process.env, TZ: 'UTC' } });
    const worker = spawnNativeStream(runtime.device, runtime.channel, 'live', {
      HIK_SDK_STREAM_TYPE: String(streamType(runtime.channel))
    });
    runtime.ffmpeg = ffmpeg;
    runtime.worker = worker;
    runtime.startedAt = new Date();
    worker.stdout.pipe(ffmpeg.stdin!);

    let stderr = '';
    let sdkStderr = '';
    ffmpeg.stderr?.on('data', (chunk) => { stderr = `${stderr}\n${String(chunk)}`.slice(-6000); });
    worker.stderr?.on('data', (chunk) => { sdkStderr = `${sdkStderr}\n${String(chunk)}`.slice(-4000); });
    console.log(`[recorder:${runtime.channel.id}] HCNetSDK live started sdk_channel=${runtime.channel.sdk_channel ?? runtime.channel.physical_channel} archive=${runtime.channel.archive_storage}`);

    const failed = (source: string, code: number | null, signal: NodeJS.Signals | null) => {
      if (runtime.stopping || runtime.generation !== generation || !this.recorders.has(runtime.channel.id)) return;
      runtime.worker?.kill('SIGTERM');
      runtime.ffmpeg?.kill('SIGTERM');
      runtime.worker = null;
      runtime.ffmpeg = null;
      runtime.restarts += 1;
      runtime.lastError = `${source} exited code=${code} signal=${signal}; ${sdkStderr.trim()} ${stderr.trim()}`.slice(-4000);
      if (runtime.restartTimer) return;
      const delay = Math.min(30_000, 1500 + runtime.restarts * 1000);
      console.warn(`[recorder:${runtime.channel.id}] native relay exited; retry in ${delay} ms: ${runtime.lastError}`);
      runtime.restartTimer = setTimeout(() => {
        runtime.restartTimer = null;
        void this.spawn(runtime).catch((error) => { runtime.lastError = error instanceof Error ? error.message : String(error); });
      }, delay);
      runtime.restartTimer.unref?.();
    };
    worker.once('exit', (code, signal) => failed('HCNetSDK worker', code, signal));
    ffmpeg.once('exit', (code, signal) => failed('ffmpeg', code, signal));
  }

  stop(channelId: string, reason: string): void {
    const runtime = this.recorders.get(channelId);
    if (!runtime) return;
    runtime.stopping = true;
    runtime.generation += 1;
    if (runtime.restartTimer) clearTimeout(runtime.restartTimer);
    runtime.worker?.kill('SIGTERM');
    runtime.ffmpeg?.kill('SIGTERM');
    this.recorders.delete(channelId);
    console.log(`[recorder:${channelId}] native relay stopped: ${reason}`);
  }

  stopAll(): void { for (const id of [...this.recorders.keys()]) this.stop(id, 'shutdown'); }

  status(channelId: string): RecorderStatus {
    const runtime = this.recorders.get(channelId);
    if (!runtime) return { channel_id: channelId, running: false, pid: null, mode: 'live-only', source_candidate: null, restarts: 0, started_at: null, last_error: null };
    return {
      channel_id: channelId,
      running: Boolean(runtime.worker && runtime.ffmpeg),
      pid: runtime.ffmpeg?.pid || runtime.worker?.pid || null,
      mode: runtime.channel.archive_storage === 'node' ? 'node-archive' : 'live-only',
      source_candidate: `hcnet-private-sdk://${runtime.device.host}:${config.nativeSdkDefaultPort}/channel/${runtime.channel.sdk_channel ?? runtime.channel.physical_channel}`,
      restarts: runtime.restarts,
      started_at: runtime.startedAt?.toISOString() || null,
      last_error: runtime.lastError
    };
  }

  allStatuses(): RecorderStatus[] { return [...this.recorders.keys()].sort().map((id) => this.status(id)); }
}
