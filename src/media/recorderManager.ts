import { spawn, type ChildProcess } from 'node:child_process';
import fs from 'node:fs/promises';
import path from 'node:path';
import { config } from '../config.js';
import type { HikvisionChannel, HikvisionDeviceConfig, RecorderStatus } from '../types.js';
import { archiveDir, liveDir } from './paths.js';
import { liveRtspCandidates, redactRtsp } from './rtsp.js';

interface RuntimeRecorder {
  key: string;
  device: HikvisionDeviceConfig;
  channel: HikvisionChannel;
  process: ChildProcess | null;
  candidateIndex: number;
  restarts: number;
  startedAt: Date | null;
  lastError: string | null;
  restartTimer: NodeJS.Timeout | null;
  stopping: boolean;
}

function channelCodec(channel: HikvisionChannel): string {
  return channel.streams.find((stream) => stream.id === channel.primary_stream_id)?.video_codec || '';
}

function recorderKey(device: HikvisionDeviceConfig, channel: HikvisionChannel): string {
  return `${device.id}|${channel.id}|${channel.primary_stream_id}|${channel.archive_storage}|${channel.enabled}`;
}

function hlsArgs(device: HikvisionDeviceConfig, channel: HikvisionChannel, input: string): { cwd: string; args: string[] } {
  const nodeArchive = channel.archive_storage === 'node';
  const cwd = nodeArchive ? archiveDir(channel.id) : liveDir(channel.id);
  const codec = channelCodec(channel);
  const transcodeVideo = config.transcodeH265 && /H\.?265|HEVC/i.test(codec);
  const segmentPattern = nodeArchive
    ? '%Y-%m-%d/%H/%Y%m%d_%H%M%S.ts'
    : 'segments/seg_%09d.ts';
  const hlsFlags = nodeArchive
    ? 'temp_file+program_date_time+omit_endlist+independent_segments'
    : 'temp_file+program_date_time+omit_endlist+independent_segments+delete_segments';

  const args = [
    '-hide_banner',
    '-loglevel', config.logLevel,
    '-rtsp_transport', config.rtspTransport,
    '-timeout', '15000000',
    '-fflags', '+genpts+discardcorrupt',
    '-i', input,
    '-map', '0:v:0',
    '-map', '0:a?'
  ];

  if (transcodeVideo) {
    args.push(
      '-c:v', 'libx264',
      '-preset', 'veryfast',
      '-tune', 'zerolatency',
      '-g', String(Math.max(25, Math.round((channel.streams[0]?.frame_rate || 25) * 2))),
      '-sc_threshold', '0'
    );
  } else {
    args.push('-c:v', 'copy');
  }
  args.push(
    '-c:a', 'aac',
    '-b:a', '64k',
    '-ac', '1',
    '-ar', '44100',
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

export class RecorderManager {
  private readonly recorders = new Map<string, RuntimeRecorder>();

  async reconcile(devices: Array<{ config: HikvisionDeviceConfig; channels: HikvisionChannel[] }>): Promise<void> {
    const wanted = new Map<string, { device: HikvisionDeviceConfig; channel: HikvisionChannel }>();
    for (const item of devices) {
      if (!item.config.enabled) continue;
      for (const channel of item.channels) {
        if (!channel.enabled) continue;
        wanted.set(channel.id, { device: item.config, channel });
      }
    }

    for (const [channelId, runtime] of this.recorders) {
      const next = wanted.get(channelId);
      if (!next || runtime.key !== recorderKey(next.device, next.channel)) {
        this.stop(channelId, next ? 'configuration changed' : 'channel removed or disabled');
      }
    }

    for (const [channelId, next] of wanted) {
      if (!this.recorders.has(channelId)) await this.start(next.device, next.channel);
    }
  }

  private async start(device: HikvisionDeviceConfig, channel: HikvisionChannel): Promise<void> {
    const runtime: RuntimeRecorder = {
      key: recorderKey(device, channel),
      device,
      channel,
      process: null,
      candidateIndex: 0,
      restarts: 0,
      startedAt: null,
      lastError: null,
      restartTimer: null,
      stopping: false
    };
    this.recorders.set(channel.id, runtime);
    await this.spawn(runtime);
  }

  private async spawn(runtime: RuntimeRecorder): Promise<void> {
    const candidates = liveRtspCandidates(runtime.device, runtime.channel);
    const input = candidates[runtime.candidateIndex % candidates.length]!;
    const { cwd, args } = hlsArgs(runtime.device, runtime.channel, input);
    await fs.mkdir(cwd, { recursive: true, mode: 0o750 });
    if (runtime.channel.archive_storage === 'device') {
      await fs.mkdir(path.join(cwd, 'segments'), { recursive: true, mode: 0o750 });
    }

    runtime.stopping = false;
    runtime.lastError = null;
    const child = spawn(config.ffmpegPath, args, { cwd, stdio: ['ignore', 'ignore', 'pipe'], env: { ...process.env, TZ: 'UTC' } });
    runtime.process = child;
    runtime.startedAt = new Date();
    let stderr = '';
    child.stderr.on('data', (chunk) => {
      stderr = `${stderr}\n${String(chunk)}`.slice(-6000);
    });
    child.once('spawn', () => {
      console.log(`[recorder:${runtime.channel.id}] started pid=${child.pid} source=${redactRtsp(input)} archive=${runtime.channel.archive_storage}`);
    });
    child.once('error', (error) => {
      runtime.lastError = error.message;
    });
    child.once('exit', (code, signal) => {
      if (runtime.process?.pid === child.pid) runtime.process = null;
      if (runtime.stopping || !this.recorders.has(runtime.channel.id)) return;
      runtime.restarts += 1;
      runtime.candidateIndex = (runtime.candidateIndex + 1) % candidates.length;
      runtime.lastError = `${stderr.trim() || `ffmpeg exited code=${code} signal=${signal}`}`.slice(-4000)
        .replace(/rtsp:\/\/[^\s/@]+(?::[^\s/@]*)?@/gi, 'rtsp://***:***@');
      const delay = Math.min(30_000, 1500 + runtime.restarts * 1000);
      console.warn(`[recorder:${runtime.channel.id}] exited; retry in ${delay} ms: ${runtime.lastError}`);
      runtime.restartTimer = setTimeout(() => {
        runtime.restartTimer = null;
        void this.spawn(runtime).catch((error) => {
          runtime.lastError = error instanceof Error ? error.message : String(error);
        });
      }, delay);
      runtime.restartTimer.unref?.();
    });
  }

  stop(channelId: string, reason: string): void {
    const runtime = this.recorders.get(channelId);
    if (!runtime) return;
    runtime.stopping = true;
    if (runtime.restartTimer) clearTimeout(runtime.restartTimer);
    runtime.process?.kill('SIGTERM');
    this.recorders.delete(channelId);
    console.log(`[recorder:${channelId}] stopped: ${reason}`);
  }

  stopAll(): void {
    for (const channelId of [...this.recorders.keys()]) this.stop(channelId, 'shutdown');
  }

  status(channelId: string): RecorderStatus {
    const runtime = this.recorders.get(channelId);
    if (!runtime) {
      return {
        channel_id: channelId,
        running: false,
        pid: null,
        mode: 'live-only',
        source_candidate: null,
        restarts: 0,
        started_at: null,
        last_error: null
      };
    }
    const candidate = liveRtspCandidates(runtime.device, runtime.channel)[runtime.candidateIndex] || null;
    return {
      channel_id: channelId,
      running: Boolean(runtime.process),
      pid: runtime.process?.pid || null,
      mode: runtime.channel.archive_storage === 'node' ? 'node-archive' : 'live-only',
      source_candidate: candidate ? redactRtsp(candidate) : null,
      restarts: runtime.restarts,
      started_at: runtime.startedAt?.toISOString() || null,
      last_error: runtime.lastError
    };
  }

  allStatuses(): RecorderStatus[] {
    return [...this.recorders.keys()].sort().map((id) => this.status(id));
  }
}
