import { execFile, spawn, type ChildProcess } from 'node:child_process';
import fs from 'node:fs/promises';
import path from 'node:path';
import { promisify } from 'node:util';
import { config } from '../config.js';
import type { HikvisionChannel, HikvisionDeviceConfig, RecorderStatus } from '../types.js';
import { archiveDir, liveDir, safeId } from '../media/paths.js';
import { spawnNativeDeviceWorker } from './client.js';
import { registerNativeDeviceRuntime, unregisterNativeDeviceRuntime } from './deviceRuntime.js';
import { emitNativeRuntimeAlarm, type NativeRuntimeAlarm } from './runtimeEvents.js';

const execFileAsync = promisify(execFile);

interface RuntimeChannel {
  channel: HikvisionChannel;
  fifoPath: string;
  ffmpeg: ChildProcess | null;
  restarts: number;
  startedAt: Date | null;
  lastError: string | null;
  restartTimer: NodeJS.Timeout | null;
}

interface RuntimeDevice {
  key: string;
  device: HikvisionDeviceConfig;
  channels: Map<string, RuntimeChannel>;
  worker: ChildProcess | null;
  runtimeRegistration: number | null;
  restarts: number;
  lastError: string | null;
  restartTimer: NodeJS.Timeout | null;
  stopping: boolean;
  generation: number;
  configPath: string;
  stdoutBuffer: string;
  stderrBuffer: string;
}

function streamType(channel: HikvisionChannel): number {
  // Persistent live is a monitoring feed. Prefer the NVR substream in native
  // mode unless the operator explicitly requests primary/main. Sixteen main
  // streams can exhaust recorder bandwidth even though RealPlay handles open.
  if (config.liveStreamPolicy !== 'primary') {
    const sub = channel.streams.find((item) => item.enabled !== false && item.stream_type === 'sub');
    if (sub) return 1;
  }
  const selected = channel.streams.find((item) => item.id === channel.primary_stream_id);
  return selected?.stream_type === 'sub' ? 1 : selected?.stream_type === 'third' ? 2 : 0;
}

function deviceKey(device: HikvisionDeviceConfig, channels: HikvisionChannel[]): string {
  return JSON.stringify({
    device: [device.id, device.host, device.username, device.password, device.enabled],
    channels: channels.map((channel) => [
      channel.id,
      channel.physical_channel,
      channel.sdk_channel,
      channel.primary_stream_id,
      channel.archive_storage,
      channel.enabled,
      channel.online,
      streamType(channel)
    ])
  });
}

function hlsArgs(channel: HikvisionChannel, fifoPath: string): { cwd: string; args: string[] } {
  const nodeArchive = channel.archive_storage === 'node';
  const cwd = nodeArchive ? archiveDir(channel.id) : liveDir(channel.id);
  const selectedType = streamType(channel);
  const selectedStream = channel.streams.find((item) => (
    selectedType === 1 ? item.stream_type === 'sub' : selectedType === 2 ? item.stream_type === 'third' : item.stream_type === 'main'
  ));
  const codec = selectedStream?.video_codec || '';
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
    '-i', fifoPath,
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

async function makeFifo(fifoPath: string): Promise<void> {
  await fs.mkdir(path.dirname(fifoPath), { recursive: true, mode: 0o750 });
  await fs.rm(fifoPath, { force: true }).catch(() => undefined);
  await execFileAsync('mkfifo', ['-m', '600', fifoPath]);
}

export class NativeSdkRecorderManager {
  private readonly devices = new Map<string, RuntimeDevice>();

  async reconcile(devices: Array<{ config: HikvisionDeviceConfig; channels: HikvisionChannel[] }>): Promise<void> {
    const wanted = new Map<string, { device: HikvisionDeviceConfig; channels: HikvisionChannel[] }>();
    for (const item of devices) {
      if (!item.config.enabled) continue;
      const channels = item.channels
        .filter((channel) => channel.enabled && channel.online !== false)
        .sort((left, right) => left.physical_channel - right.physical_channel);
      if (channels.length) wanted.set(item.config.id, { device: item.config, channels });
    }

    for (const [deviceId, runtime] of this.devices) {
      const next = wanted.get(deviceId);
      if (!next || runtime.key !== deviceKey(next.device, next.channels)) {
        this.stopDevice(deviceId, next ? 'configuration changed' : 'device removed or all channels offline');
      }
    }

    for (const [deviceId, next] of wanted) {
      if (!this.devices.has(deviceId)) await this.startDevice(next.device, next.channels);
    }
  }

  private async startDevice(device: HikvisionDeviceConfig, channels: HikvisionChannel[]): Promise<void> {
    const runtimeRoot = path.join(config.tempRoot, 'native-device-runtime', safeId(device.id));
    await fs.mkdir(runtimeRoot, { recursive: true, mode: 0o750 });
    const runtime: RuntimeDevice = {
      key: deviceKey(device, channels),
      device,
      channels: new Map(),
      worker: null,
      runtimeRegistration: null,
      restarts: 0,
      lastError: null,
      restartTimer: null,
      stopping: false,
      generation: 0,
      configPath: path.join(runtimeRoot, 'live.tsv'),
      stdoutBuffer: '',
      stderrBuffer: ''
    };

    for (const channel of channels) {
      runtime.channels.set(channel.id, {
        channel,
        fifoPath: path.join(runtimeRoot, `${safeId(channel.id)}.fifo`),
        ffmpeg: null,
        restarts: 0,
        startedAt: null,
        lastError: null,
        restartTimer: null
      });
    }
    this.devices.set(device.id, runtime);
    await this.spawnDevice(runtime);
  }

  private async prepareRuntime(runtime: RuntimeDevice): Promise<void> {
    const rows: string[] = [];
    for (const channelRuntime of runtime.channels.values()) {
      await makeFifo(channelRuntime.fifoPath);
      const channel = channelRuntime.channel;
      rows.push([
        channel.physical_channel,
        channel.sdk_channel ?? channel.physical_channel,
        streamType(channel),
        channelRuntime.fifoPath
      ].join('\t'));
      await this.spawnFfmpeg(runtime, channelRuntime);
    }
    await fs.writeFile(runtime.configPath, `${rows.join('\n')}\n`, { mode: 0o600 });
  }

  private async spawnDevice(runtime: RuntimeDevice): Promise<void> {
    const generation = ++runtime.generation;
    runtime.stopping = false;
    runtime.lastError = null;
    runtime.stdoutBuffer = '';
    runtime.stderrBuffer = '';
    await this.prepareRuntime(runtime);

    const worker = spawnNativeDeviceWorker(runtime.device, runtime.configPath);
    runtime.worker = worker;
    runtime.runtimeRegistration = registerNativeDeviceRuntime(runtime.device.id, worker);
    console.log(`[hcnetsdk-device:${runtime.device.id}] grouped runtime started channels=${runtime.channels.size}`);

    worker.stdout?.on('data', (chunk) => {
      runtime.stdoutBuffer += String(chunk);
      for (;;) {
        const newline = runtime.stdoutBuffer.indexOf('\n');
        if (newline < 0) break;
        const line = runtime.stdoutBuffer.slice(0, newline).trim();
        runtime.stdoutBuffer = runtime.stdoutBuffer.slice(newline + 1);
        if (!line.startsWith('{')) continue;
        try { emitNativeRuntimeAlarm(runtime.device.id, JSON.parse(line) as NativeRuntimeAlarm); }
        catch (error) { console.warn(`[hcnetsdk-device:${runtime.device.id}] event parse failed`, error instanceof Error ? error.message : error); }
      }
      if (runtime.stdoutBuffer.length > 1024 * 1024) runtime.stdoutBuffer = runtime.stdoutBuffer.slice(-64 * 1024);
    });

    worker.stderr?.on('data', (chunk) => {
      runtime.stderrBuffer += String(chunk);
      for (;;) {
        const newline = runtime.stderrBuffer.indexOf('\n');
        if (newline < 0) break;
        const line = runtime.stderrBuffer.slice(0, newline).trim();
        runtime.stderrBuffer = runtime.stderrBuffer.slice(newline + 1);
        if (!line) continue;
        console.log(`[hcnetsdk-device:${runtime.device.id}] ${line}`);
        const failed = line.match(/NET_DVR_RealPlay_V40 failed physical=(\d+) sdk=(\d+) HCNetSDK error=(\d+)/);
        if (failed) {
          const physical = Number(failed[1]);
          const channelRuntime = [...runtime.channels.values()].find((item) => item.channel.physical_channel === physical);
          if (channelRuntime) channelRuntime.lastError = `NET_DVR_RealPlay_V40 HCNetSDK error=${failed[3]}`;
        }
      }
      if (runtime.stderrBuffer.length > 1024 * 1024) runtime.stderrBuffer = runtime.stderrBuffer.slice(-64 * 1024);
    });

    worker.once('exit', (code, signal) => {
      if (runtime.runtimeRegistration !== null) {
        unregisterNativeDeviceRuntime(runtime.device.id, runtime.runtimeRegistration);
        runtime.runtimeRegistration = null;
      }
      if (runtime.stopping || runtime.generation !== generation || !this.devices.has(runtime.device.id)) return;
      runtime.worker = null;
      runtime.restarts += 1;
      runtime.lastError = `grouped HCNetSDK worker exited code=${code} signal=${signal}; ${runtime.stderrBuffer.trim()}`.slice(-4000);
      for (const channelRuntime of runtime.channels.values()) {
        channelRuntime.ffmpeg?.kill('SIGTERM');
        channelRuntime.ffmpeg = null;
      }
      if (runtime.restartTimer) return;
      const delay = Math.min(60_000, 5000 + runtime.restarts * 5000);
      console.warn(`[hcnetsdk-device:${runtime.device.id}] grouped runtime exited; retry in ${delay} ms: ${runtime.lastError}`);
      runtime.restartTimer = setTimeout(() => {
        runtime.restartTimer = null;
        void this.spawnDevice(runtime).catch((error) => {
          runtime.lastError = error instanceof Error ? error.message : String(error);
        });
      }, delay);
      runtime.restartTimer.unref?.();
    });
  }

  private async spawnFfmpeg(deviceRuntime: RuntimeDevice, channelRuntime: RuntimeChannel): Promise<void> {
    if (channelRuntime.restartTimer) {
      clearTimeout(channelRuntime.restartTimer);
      channelRuntime.restartTimer = null;
    }
    const { cwd, args } = hlsArgs(channelRuntime.channel, channelRuntime.fifoPath);
    await fs.mkdir(cwd, { recursive: true, mode: 0o750 });
    if (channelRuntime.channel.archive_storage === 'device') {
      await fs.mkdir(path.join(cwd, 'segments'), { recursive: true, mode: 0o750 });
    }
    const ffmpeg = spawn(config.ffmpegPath, args, {
      cwd,
      stdio: ['ignore', 'ignore', 'pipe'],
      env: { ...process.env, TZ: 'UTC' }
    });
    channelRuntime.ffmpeg = ffmpeg;
    channelRuntime.startedAt = new Date();
    let stderr = '';
    ffmpeg.stderr?.on('data', (chunk) => { stderr = `${stderr}\n${String(chunk)}`.slice(-6000); });
    ffmpeg.once('error', (error) => {
      channelRuntime.lastError = error.message;
    });
    ffmpeg.once('exit', (code, signal) => {
      if (deviceRuntime.stopping || !this.devices.has(deviceRuntime.device.id)) return;
      if (channelRuntime.ffmpeg !== ffmpeg) return;
      channelRuntime.ffmpeg = null;
      channelRuntime.restarts += 1;
      channelRuntime.lastError = `ffmpeg exited code=${code} signal=${signal}; ${stderr.trim()}`.slice(-3000);
      const delay = Math.min(30_000, 2000 + channelRuntime.restarts * 1000);
      channelRuntime.restartTimer = setTimeout(() => {
        channelRuntime.restartTimer = null;
        if (!deviceRuntime.stopping && deviceRuntime.worker?.exitCode === null) {
          void this.spawnFfmpeg(deviceRuntime, channelRuntime).catch((error) => {
            channelRuntime.lastError = error instanceof Error ? error.message : String(error);
          });
        }
      }, delay);
      channelRuntime.restartTimer.unref?.();
    });
  }

  private stopDevice(deviceId: string, reason: string): void {
    const runtime = this.devices.get(deviceId);
    if (!runtime) return;
    runtime.stopping = true;
    runtime.generation += 1;
    if (runtime.restartTimer) clearTimeout(runtime.restartTimer);
    if (runtime.runtimeRegistration !== null) {
      unregisterNativeDeviceRuntime(deviceId, runtime.runtimeRegistration);
      runtime.runtimeRegistration = null;
    }
    runtime.worker?.kill('SIGTERM');
    for (const channelRuntime of runtime.channels.values()) {
      if (channelRuntime.restartTimer) clearTimeout(channelRuntime.restartTimer);
      channelRuntime.ffmpeg?.kill('SIGTERM');
    }
    this.devices.delete(deviceId);
    console.log(`[hcnetsdk-device:${deviceId}] grouped runtime stopped: ${reason}`);
  }

  stop(channelId: string, reason: string): void {
    for (const [deviceId, runtime] of this.devices) {
      if (runtime.channels.has(channelId)) {
        this.stopDevice(deviceId, `${reason}; channel=${channelId}`);
        return;
      }
    }
  }

  stopAll(): void {
    for (const id of [...this.devices.keys()]) this.stopDevice(id, 'shutdown');
  }

  status(channelId: string): RecorderStatus {
    for (const runtime of this.devices.values()) {
      const channelRuntime = runtime.channels.get(channelId);
      if (!channelRuntime) continue;
      const running = Boolean(runtime.worker && runtime.worker.exitCode === null && channelRuntime.ffmpeg && channelRuntime.ffmpeg.exitCode === null);
      return {
        channel_id: channelId,
        running,
        pid: channelRuntime.ffmpeg?.pid || runtime.worker?.pid || null,
        mode: channelRuntime.channel.archive_storage === 'node' ? 'node-archive' : 'live-only',
        source_candidate: `hcnet-private-sdk://${runtime.device.host}:${config.nativeSdkDefaultPort}/channel/${channelRuntime.channel.sdk_channel ?? channelRuntime.channel.physical_channel}/stream/${streamType(channelRuntime.channel)}`,
        restarts: runtime.restarts + channelRuntime.restarts,
        started_at: channelRuntime.startedAt?.toISOString() || null,
        last_error: channelRuntime.lastError || runtime.lastError
      };
    }
    return { channel_id: channelId, running: false, pid: null, mode: 'live-only', source_candidate: null, restarts: 0, started_at: null, last_error: null };
  }

  allStatuses(): RecorderStatus[] {
    return [...this.devices.values()]
      .flatMap((runtime) => [...runtime.channels.keys()])
      .sort()
      .map((id) => this.status(id));
  }
}
