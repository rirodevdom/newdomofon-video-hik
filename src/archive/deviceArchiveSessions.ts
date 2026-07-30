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
  status: 'preparing' | 'ready' | 'error' | 'cancelled';
  error: string | null;
  cancelled: boolean;
  createdAt: number;
  lastAccessAt: number;
}

const CANDIDATE_STARTUP_TIMEOUT_MS = 8_000;
const SESSION_READY_TIMEOUT_MS = 25_000;
const PROCESS_STOP_GRACE_MS = 1_000;

async function exists(file: string): Promise<boolean> {
  try {
    await fs.access(file);
    return true;
  } catch {
    return false;
  }
}

export function shouldRetireArchiveSession(
  session: Pick<ArchiveSession, 'id' | 'channelId' | 'status'>,
  channelId: string,
  keepId: string
): boolean {
  return session.channelId === channelId && session.id !== keepId && session.status !== 'cancelled';
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
    for (const session of this.sessions.values()) {
      session.cancelled = true;
      session.status = 'cancelled';
      session.process?.kill('SIGTERM');
    }
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
    if (existing && !existing.cancelled && existing.status !== 'error') {
      existing.lastAccessAt = Date.now();
      await this.waitReady(existing);
      return existing;
    }

    if (existing) {
      await this.terminateAndRemove(existing, 'Restarting failed archive session');
    }
    await this.retireSuperseded(channel.id, id);

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
      cancelled: false,
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
    if (!session || session.channelId !== channelId || session.cancelled || session.status === 'error') return null;
    session.lastAccessAt = Date.now();
    return session;
  }

  private async retireSuperseded(channelId: string, keepId: string): Promise<void> {
    const stale = [...this.sessions.values()].filter((session) => shouldRetireArchiveSession(session, channelId, keepId));
    for (const session of stale) {
      console.log(`[device-archive:${channelId}] cancelling superseded session ${session.id}`);
      await this.terminateAndRemove(session, 'Superseded by a newer archive seek');
    }
  }

  private async terminateAndRemove(session: ArchiveSession, reason: string): Promise<void> {
    session.cancelled = true;
    session.status = 'cancelled';
    session.error = reason;
    const child = session.process;
    session.process = null;

    if (child && child.exitCode === null) {
      await new Promise<void>((resolve) => {
        const timer = setTimeout(resolve, PROCESS_STOP_GRACE_MS);
        child.once('exit', () => {
          clearTimeout(timer);
          resolve();
        });
        child.kill('SIGTERM');
      });
      if (child.exitCode === null) child.kill('SIGKILL');
    }

    this.sessions.delete(session.id);
    await fs.rm(session.dir, { recursive: true, force: true }).catch(() => undefined);
  }

  private async spawn(device: HikvisionDeviceConfig, channel: HikvisionChannel, session: ArchiveSession): Promise<void> {
    const candidates = await devicePlaybackCandidates(device, channel, session.start, session.end);
    if (session.cancelled) return;
    if (!candidates.length) throw new Error('No device archive playback candidates');
    const duration = Math.max(1, Math.ceil((session.end.getTime() - session.start.getTime()) / 1000));

    const attempt = (index: number): void => {
      if (session.cancelled) return;
      const input = candidates[index];
      if (!input) {
        session.status = 'error';
        session.error = 'All device archive playback candidates failed';
        return;
      }

      const startedAt = Date.now();
      console.log(`[device-archive:${channel.id}] starting candidate ${index + 1}/${candidates.length} for session ${session.id}`);
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
      let completed = false;
      child.stderr.on('data', (chunk) => { stderr = `${stderr}\n${String(chunk)}`.slice(-5000); });

      const finishAttempt = async (code: number | null, timedOut = false): Promise<void> => {
        if (completed) return;
        completed = true;
        clearTimeout(startupTimer);
        if (timedOut && child.exitCode === null) child.kill('SIGTERM');
        if (session.process === child) session.process = null;
        if (session.cancelled) return;

        if (await exists(session.playlist)) {
          session.status = 'ready';
          console.log(`[device-archive:${channel.id}] session ${session.id} ready in ${Date.now() - startedAt} ms`);
          return;
        }

        if (index + 1 < candidates.length) {
          console.warn(`[device-archive:${channel.id}] candidate ${index + 1} failed before playlist${timedOut ? ' (startup timeout)' : ` (exit ${code})`}; trying next`);
          await fs.rm(session.dir, { recursive: true, force: true });
          await fs.mkdir(session.dir, { recursive: true, mode: 0o750 });
          attempt(index + 1);
          return;
        }

        session.status = 'error';
        session.error = stderr.trim() || (timedOut
          ? `Archive candidate startup exceeded ${CANDIDATE_STARTUP_TIMEOUT_MS} ms`
          : `ffmpeg exited ${code}`);
      };

      const startupTimer = setTimeout(() => {
        void (async () => {
          if (session.cancelled || await exists(session.playlist)) return;
          await finishAttempt(null, true);
        })();
      }, CANDIDATE_STARTUP_TIMEOUT_MS);

      child.once('exit', (code) => { void finishAttempt(code); });
    };
    attempt(0);
  }

  private async waitReady(session: ArchiveSession): Promise<void> {
    const deadline = Date.now() + SESSION_READY_TIMEOUT_MS;
    while (Date.now() < deadline) {
      if (session.cancelled || session.status === 'cancelled') {
        throw new Error(session.error || 'Archive session was superseded by a newer seek');
      }
      if (session.status === 'error') throw new Error(session.error || 'Device archive session failed');
      if (await exists(session.playlist)) {
        session.status = 'ready';
        return;
      }
      await new Promise((resolve) => setTimeout(resolve, 200));
    }

    await this.terminateAndRemove(session, 'Device archive session did not produce a playlist in time');
    throw new Error('Device archive session did not produce a playlist in time');
  }

  private async cleanup(): Promise<void> {
    const now = Date.now();
    for (const session of [...this.sessions.values()]) {
      if (now - session.lastAccessAt < config.deviceArchiveSessionKeepMs) continue;
      await this.terminateAndRemove(session, 'Archive session expired');
    }
  }
}
