import crypto from 'node:crypto';
import { spawn, type ChildProcess } from 'node:child_process';
import fs from 'node:fs/promises';
import path from 'node:path';
import { config } from '../config.js';
import type { HikvisionChannel, HikvisionDeviceConfig } from '../types.js';
import { safeId } from '../media/paths.js';
import { devicePlaybackCandidates } from './deviceArchive.js';

interface ArchiveSession {
  id: string;
  channelId: string;
  deviceId: string;
  start: Date;
  end: Date;
  dir: string;
  playlist: string;
  process: ChildProcess | null;
  status: 'preparing' | 'ready' | 'error';
  error: string | null;
  createdAt: number;
  lastAccessAt: number;
}

async function exists(file: string): Promise<boolean> {
  try {
    await fs.access(file);
    return true;
  } catch {
    return false;
  }
}

export class DeviceArchiveSessionManager {
  private readonly sessions = new Map<string, ArchiveSession>();
  private cleanupTimer: NodeJS.Timeout | null = null;

  startCleanup(): void {
    if (this.cleanupTimer) return;
    this.cleanupTimer = setInterval(() => { void this.cleanup(); }, 60_000);
    this.cleanupTimer.unref?.();
  }

  stop(): void {
    if (this.cleanupTimer) clearInterval(this.cleanupTimer);
    this.cleanupTimer = null;
    for (const session of this.sessions.values()) session.process?.kill('SIGTERM');
    this.sessions.clear();
  }

  private sessionKey(channelId: string, start: Date, end: Date): string {
    return crypto.createHash('sha256').update(`${channelId}|${start.toISOString()}|${end.toISOString()}`).digest('hex').slice(0, 24);
  }

  async getOrCreate(
    device: HikvisionDeviceConfig,
    channel: HikvisionChannel,
    start: Date,
    requestedEnd: Date
  ): Promise<ArchiveSession> {
    const maxEnd = new Date(start.getTime() + config.deviceArchiveSessionSeconds * 1000);
    const end = requestedEnd > maxEnd ? maxEnd : requestedEnd;
    const id = this.sessionKey(channel.id, start, end);
    const existing = this.sessions.get(id);
    if (existing && existing.status !== 'error') {
      existing.lastAccessAt = Date.now();
      await this.waitReady(existing);
      return existing;
    }

    const dir = path.join(config.tempRoot, 'device-archive', safeId(channel.id), id);
    await fs.mkdir(dir, { recursive: true, mode: 0o750 });
    const session: ArchiveSession = {
      id,
      channelId: channel.id,
      deviceId: device.id,
      start,
      end,
      dir,
      playlist: path.join(dir, 'index.m3u8'),
      process: null,
      status: 'preparing',
      error: null,
      createdAt: Date.now(),
      lastAccessAt: Date.now()
    };
    this.sessions.set(id, session);
    await this.spawn(device, channel, session);
    await this.waitReady(session);
    return session;
  }

  get(channelId: string, sessionId: string): ArchiveSession | null {
    const session = this.sessions.get(sessionId);
    if (!session || session.channelId !== channelId) return null;
    session.lastAccessAt = Date.now();
    return session;
  }

  private async spawn(device: HikvisionDeviceConfig, channel: HikvisionChannel, session: ArchiveSession): Promise<void> {
    const candidates = await devicePlaybackCandidates(device, channel, session.start, session.end);
    if (!candidates.length) throw new Error('No device archive playback candidates');
    const duration = Math.max(1, Math.ceil((session.end.getTime() - session.start.getTime()) / 1000));

    const attempt = (index: number): void => {
      const input = candidates[index];
      if (!input) {
        session.status = 'error';
        session.error = 'All device archive playback candidates failed';
        return;
      }
      const child = spawn(config.ffmpegPath, [
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
      ], { cwd: session.dir, stdio: ['ignore', 'ignore', 'pipe'] });
      session.process = child;
      let stderr = '';
      child.stderr.on('data', (chunk) => { stderr = `${stderr}\n${String(chunk)}`.slice(-5000); });
      child.once('exit', async (code) => {
        session.process = null;
        if (await exists(session.playlist)) {
          session.status = 'ready';
          return;
        }
        if (index + 1 < candidates.length) {
          await fs.rm(session.dir, { recursive: true, force: true });
          await fs.mkdir(session.dir, { recursive: true, mode: 0o750 });
          attempt(index + 1);
          return;
        }
        session.status = 'error';
        session.error = stderr.trim() || `ffmpeg exited ${code}`;
      });
    };
    attempt(0);
  }

  private async waitReady(session: ArchiveSession): Promise<void> {
    const deadline = Date.now() + 25_000;
    while (Date.now() < deadline) {
      if (session.status === 'error') throw new Error(session.error || 'Device archive session failed');
      if (await exists(session.playlist)) {
        session.status = 'ready';
        return;
      }
      await new Promise((resolve) => setTimeout(resolve, 250));
    }
    session.process?.kill('SIGTERM');
    session.process = null;
    session.status = 'error';
    session.error = 'Device archive session did not produce a playlist in time';
    throw new Error(session.error);
  }

  private async cleanup(): Promise<void> {
    const now = Date.now();
    for (const [id, session] of this.sessions) {
      if (now - session.lastAccessAt < config.deviceArchiveSessionKeepMs) continue;
      session.process?.kill('SIGTERM');
      this.sessions.delete(id);
      await fs.rm(session.dir, { recursive: true, force: true }).catch(() => undefined);
    }
  }
}
