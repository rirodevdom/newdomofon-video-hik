import crypto from 'node:crypto';
import { execFile, spawn, type ChildProcess } from 'node:child_process';
import fs from 'node:fs/promises';
import path from 'node:path';
import { promisify } from 'node:util';
import { config } from '../config.js';
import type { HikvisionChannel, HikvisionDeviceConfig } from '../types.js';
import { safeId } from '../media/paths.js';
import { sdkChannel } from './client.js';
import { startGroupedPlayback, stopGroupedPlayback } from './deviceRuntime.js';

const execFileAsync = promisify(execFile);

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
  ffmpeg: ChildProcess | null;
  fifoPath: string;
  playbackStarted: boolean;
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

async function makeFifo(fifoPath: string): Promise<void> {
  await fs.rm(fifoPath, { force: true }).catch(() => undefined);
  await execFileAsync('mkfifo', ['-m', '600', fifoPath]);
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
      if (session.playbackStarted) void stopGroupedPlayback(session.deviceId, session.id);
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

    // Commands are delivered to the same persistent HCNetSDK process that owns
    // live and alarms for this DVR. STOP then PLAYBACK are ordered on one stdin,
    // so a seek never creates overlapping NET_DVR_Init/login processes.
    await this.retireChannel(channel.id, id);

    const dir = path.join(config.tempRoot, 'native-device-archive', safeId(channel.id), id);
    await fs.rm(dir, { recursive: true, force: true });
    await fs.mkdir(dir, { recursive: true, mode: 0o750 });
    const session: RuntimeSession = {
      id, channelId: channel.id, deviceId: device.id, start, end, dir,
      playlist: path.join(dir, 'index.m3u8'),
      status: 'preparing', error: null, createdAt: Date.now(), lastAccessAt: Date.now(),
      ffmpeg: null,
      fifoPath: path.join(dir, 'playback.ps.fifo'),
      playbackStarted: false,
      retiredAt: null
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
      const rawStatus = error && typeof error === 'object' && 'statusCode' in error
        ? Number((error as { statusCode?: unknown }).statusCode)
        : 502;
      const statusCode = Number.isInteger(rawStatus) && rawStatus >= 400 && rawStatus <= 599 ? rawStatus : 502;
      throw Object.assign(new Error(session.error), { statusCode });
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
    await makeFifo(session.fifoPath);
    const ffmpeg = spawn(config.ffmpegPath, [
      '-hide_banner', '-loglevel', config.logLevel,
      '-fflags', '+genpts+discardcorrupt',
      '-probesize', '2097152', '-analyzeduration', '2000000',
      '-f', 'mpeg', '-i', session.fifoPath,
      '-t', String(duration),
      '-map', '0:v:0', '-map', '0:a?',
      '-c:v', 'libx264', '-preset', 'veryfast', '-tune', 'zerolatency', '-g', '50', '-sc_threshold', '0',
      '-c:a', 'aac', '-b:a', '64k',
      '-f', 'hls', '-hls_time', '2', '-hls_list_size', '0',
      '-hls_flags', 'temp_file+program_date_time+independent_segments',
      '-hls_segment_filename', 'seg_%06d.ts',
      'index.m3u8'
    ], { cwd: session.dir, stdio: ['ignore', 'ignore', 'pipe'], env: { ...process.env, TZ: 'UTC' } });
    session.ffmpeg = ffmpeg;

    let errors = '';
    ffmpeg.stderr?.on('data', (chunk) => { errors = `${errors}\n${String(chunk)}`.slice(-5000); });
    ffmpeg.once('error', (error) => {
      if (session.status === 'preparing') {
        session.status = 'error';
        session.error = error.message;
      }
    });
    ffmpeg.once('exit', (code, signal) => {
      if (session.status === 'retired' || session.status === 'cancelled') return;
      if (session.status === 'preparing') {
        session.status = 'error';
        session.error = errors.trim() || `archive ffmpeg exited code=${code} signal=${signal}`;
      }
    });

    await startGroupedPlayback({
      deviceId: device.id,
      sessionId: session.id,
      sdkChannel: sdkChannel(channel),
      start: session.start,
      end: session.end,
      fifoPath: session.fifoPath
    });
    session.playbackStarted = true;
  }

  private async waitReady(session: RuntimeSession): Promise<void> {
    const deadline = Date.now() + READY_TIMEOUT_MS;
    while (Date.now() < deadline) {
      if (session.status === 'error') throw Object.assign(new Error(session.error || 'HCNetSDK archive session failed'), { statusCode: 502 });
      if (await playable(session)) {
        session.status = 'ready';
        return;
      }
      await new Promise((resolve) => setTimeout(resolve, 100));
    }
    throw Object.assign(new Error('HCNetSDK grouped playback did not produce a playable HLS segment in time'), { statusCode: 503 });
  }

  private async stopRuntime(session: RuntimeSession): Promise<void> {
    if (session.playbackStarted) {
      session.playbackStarted = false;
      await stopGroupedPlayback(session.deviceId, session.id).catch(() => undefined);
    }
    await stopChild(session.ffmpeg);
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
