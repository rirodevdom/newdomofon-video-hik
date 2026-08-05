import crypto from 'node:crypto';
import { spawn, type ChildProcess } from 'node:child_process';
import fs from 'node:fs/promises';
import path from 'node:path';
import { config } from '../config.js';
import type { HikvisionChannel, HikvisionDeviceConfig } from '../types.js';
import { safeId } from '../media/paths.js';
import { spawnNativeStream } from './client.js';

export interface NativeArchiveSession {
  id: string;
  channelId: string;
  deviceId: string;
  start: Date;
  end: Date;
  dir: string;
  playlist: string;
  status: 'preparing' | 'ready' | 'error' | 'retired' | 'cancelled';
  error: string | null;
  createdAt: number;
  lastAccessAt: number;
}

interface RuntimeSession extends NativeArchiveSession {
  worker: ChildProcess | null;
  ffmpeg: ChildProcess | null;
  retiredAt: number | null;
}

const READY_TIMEOUT_MS = 20_000;
const RETIRED_GRACE_MS = 45_000;
const CLEANUP_INTERVAL_MS = 10_000;

async function playable(session: RuntimeSession): Promise<boolean> {
  try {
    const body = await fs.readFile(session.playlist, 'utf8');
    const media = body.split(/\r?\n/).map((line) => line.trim()).find((line) => line && !line.startsWith('#'));
    if (!media) return false;
    await fs.access(path.join(session.dir, media.split('?')[0]!));
    return true;
  } catch {
    return false;
  }
}

async function stopChild(child: ChildProcess | null): Promise<void> {
  if (!child || child.exitCode !== null) return;
  child.kill('SIGTERM');
  await new Promise((resolve) => setTimeout(resolve, 250));
  if (child.exitCode === null) child.kill('SIGKILL');
}

export class NativeSdkArchiveSessionManager {
  private readonly sessions = new Map<string, RuntimeSession>();
  private cleanupTimer: NodeJS.Timeout | null = null;

  startCleanup(): void {
    if (this.cleanupTimer) return;
    this.cleanupTimer = setInterval(() => { void this.cleanup(); }, CLEANUP_INTERVAL_MS);
    this.cleanupTimer.unref?.();
  }

  stop(): void {
    if (this.cleanupTimer) clearInterval(this.cleanupTimer);
    this.cleanupTimer = null;
    for (const session of this.sessions.values()) {
      session.status = 'cancelled';
      session.worker?.kill('SIGTERM');
      session.ffmpeg?.kill('SIGTERM');
    }
    this.sessions.clear();
  }

  private key(channelId: string, start: Date, end: Date): string {
    return crypto.createHash('sha256').update(`${channelId}|${start.toISOString()}|${end.toISOString()}`).digest('hex').slice(0, 24);
  }

  async getOrCreate(device: HikvisionDeviceConfig, channel: HikvisionChannel, start: Date, requestedEnd: Date): Promise<NativeArchiveSession> {
    const maxEnd = new Date(start.getTime() + config.deviceArchiveSessionSeconds * 1000);
    const end = requestedEnd > maxEnd ? maxEnd : requestedEnd;
    const id = this.key(channel.id, start, end);
    const existing = this.sessions.get(id);
    if (existing && existing.status !== 'error' && existing.status !== 'cancelled' && existing.status !== 'retired') {
      existing.lastAccessAt = Date.now();
      await this.waitReady(existing);
      return existing;
    }

    // HCNetSDK/NVR playback resources are finite. Release every older upstream
    // playback handle for this channel before opening the replacement. Its HLS
    // files remain in the grace window so browsers finishing old requests do
    // not receive 404s.
    await this.retireChannel(channel.id, id);

    const dir = path.join(config.tempRoot, 'native-device-archive', safeId(channel.id), id);
    await fs.rm(dir, { recursive: true, force: true });
    await fs.mkdir(dir, { recursive: true, mode: 0o750 });
    const session: RuntimeSession = {
      id, channelId: channel.id, deviceId: device.id, start, end, dir,
      playlist: path.join(dir, 'index.m3u8'),
      status: 'preparing', error: null, createdAt: Date.now(), lastAccessAt: Date.now(),
      worker: null, ffmpeg: null, retiredAt: null
    };
    this.sessions.set(id, session);
    try {
      await this.spawn(device, channel, session);
      await this.waitReady(session);
      return session;
    } catch (error) {
      session.status = 'error';
      session.error = error instanceof Error ? error.message : String(error);
      await this.stopRuntime(session);
      throw Object.assign(new Error(session.error), { statusCode: 502 });
    }
  }

  get(channelId: string, sessionId: string): NativeArchiveSession | null {
    const session = this.sessions.get(sessionId);
    if (!session || session.channelId !== channelId || session.status === 'error' || session.status === 'cancelled') return null;
    session.lastAccessAt = Date.now();
    return session;
  }

  private async spawn(device: HikvisionDeviceConfig, channel: HikvisionChannel, session: RuntimeSession): Promise<void> {
    const duration = Math.max(1, Math.ceil((session.end.getTime() - session.start.getTime()) / 1000));
    const ffmpeg = spawn(config.ffmpegPath, [
      '-hide_banner', '-loglevel', config.logLevel,
      '-fflags', '+genpts+discardcorrupt',
      '-probesize', '2097152', '-analyzeduration', '2000000',
      '-f', 'mpeg', '-i', 'pipe:0',
      '-t', String(duration),
      '-map', '0:v:0', '-map', '0:a?',
      '-c:v', 'libx264', '-preset', 'veryfast', '-tune', 'zerolatency', '-g', '50', '-sc_threshold', '0',
      '-c:a', 'aac', '-b:a', '64k',
      '-f', 'hls', '-hls_time', '2', '-hls_list_size', '0',
      '-hls_flags', 'temp_file+program_date_time+independent_segments',
      '-hls_segment_filename', 'seg_%06d.ts',
      'index.m3u8'
    ], { cwd: session.dir, stdio: ['pipe', 'ignore', 'pipe'], env: { ...process.env, TZ: 'UTC' } });
    const worker = spawnNativeStream(device, channel, 'playback', {
      HIK_SDK_START: session.start.toISOString(),
      HIK_SDK_END: session.end.toISOString(),
      HIK_SDK_STREAM_TYPE: '0'
    });
    session.ffmpeg = ffmpeg;
    session.worker = worker;
    worker.stdout?.pipe(ffmpeg.stdin!);

    let errors = '';
    worker.stderr?.on('data', (chunk) => { errors = `${errors}\n${String(chunk)}`.slice(-5000); });
    ffmpeg.stderr?.on('data', (chunk) => { errors = `${errors}\n${String(chunk)}`.slice(-5000); });
    const onExit = () => {
      if (session.status === 'retired' || session.status === 'cancelled') return;
      if (session.status === 'preparing') {
        session.status = 'error';
        session.error = errors.trim() || 'HCNetSDK archive pipeline exited before becoming playable';
      }
    };
    worker.once('exit', onExit);
    ffmpeg.once('exit', onExit);
  }

  private async waitReady(session: RuntimeSession): Promise<void> {
    const deadline = Date.now() + READY_TIMEOUT_MS;
    while (Date.now() < deadline) {
      if (session.status === 'error') throw new Error(session.error || 'HCNetSDK archive session failed');
      if (await playable(session)) {
        session.status = 'ready';
        return;
      }
      await new Promise((resolve) => setTimeout(resolve, 100));
    }
    throw Object.assign(new Error('HCNetSDK playback did not produce a playable HLS segment in time'), { statusCode: 503 });
  }

  private async stopRuntime(session: RuntimeSession): Promise<void> {
    await Promise.all([stopChild(session.worker), stopChild(session.ffmpeg)]);
    session.worker = null;
    session.ffmpeg = null;
  }

  private async retireChannel(channelId: string, keepId: string): Promise<void> {
    for (const session of this.sessions.values()) {
      if (session.channelId !== channelId || session.id === keepId || session.status === 'retired') continue;
      session.status = 'retired';
      session.retiredAt = Date.now();
      await this.stopRuntime(session);
    }
  }

  private async cleanup(): Promise<void> {
    const now = Date.now();
    for (const session of [...this.sessions.values()]) {
      const retiredExpired = session.status === 'retired' && session.retiredAt !== null && now - session.retiredAt >= RETIRED_GRACE_MS;
      const idleExpired = session.status !== 'retired' && now - session.lastAccessAt >= config.deviceArchiveSessionKeepMs;
      if (!retiredExpired && !idleExpired) continue;
      session.status = 'cancelled';
      await this.stopRuntime(session);
      this.sessions.delete(session.id);
      await fs.rm(session.dir, { recursive: true, force: true }).catch(() => undefined);
    }
  }
}
