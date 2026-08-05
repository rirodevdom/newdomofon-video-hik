import crypto from 'node:crypto';
import { execFile, spawn } from 'node:child_process';
import fs from 'node:fs/promises';
import path from 'node:path';
import { promisify } from 'node:util';
import type { Response } from 'express';
import { config } from '../config.js';
import type { ArchiveRange, HikvisionChannel, HikvisionDeviceConfig } from '../types.js';
import { safeId } from '../media/paths.js';
import { findNativeArchive, sdkChannel } from './client.js';
import { startGroupedPlayback, stopGroupedPlayback } from './deviceRuntime.js';

const execFileAsync = promisify(execFile);

export async function nativeArchiveRanges(
  device: HikvisionDeviceConfig,
  channel: HikvisionChannel,
  start: Date,
  end: Date
): Promise<ArchiveRange[]> {
  const items = await findNativeArchive(device, channel, start, end);
  return items.map((item) => ({
    start: item.start,
    end: item.end,
    source: 'device' as const
  }));
}

export async function streamNativeArchiveMp4(
  device: HikvisionDeviceConfig,
  channel: HikvisionChannel,
  start: Date,
  end: Date,
  res: Response
): Promise<void> {
  const duration = Math.max(1, Math.ceil((end.getTime() - start.getTime()) / 1000));
  const sessionId = `export-${crypto.randomUUID()}`;
  const dir = path.join(config.tempRoot, 'native-device-export', safeId(channel.id), safeId(sessionId));
  const fifoPath = path.join(dir, 'playback.ps.fifo');
  await fs.mkdir(dir, { recursive: true, mode: 0o750 });
  await execFileAsync('mkfifo', ['-m', '600', fifoPath]);

  const ffmpeg = spawn(config.ffmpegPath, [
    '-hide_banner', '-loglevel', config.logLevel,
    '-fflags', '+genpts+discardcorrupt',
    '-probesize', '2097152', '-analyzeduration', '2000000',
    '-f', 'mpeg', '-i', fifoPath,
    '-t', String(duration),
    '-map', '0:v:0', '-map', '0:a?',
    '-c:v', 'libx264', '-preset', 'veryfast',
    '-c:a', 'aac', '-b:a', '64k',
    '-movflags', 'frag_keyframe+empty_moov+default_base_moof',
    '-f', 'mp4', 'pipe:1'
  ], { stdio: ['ignore', 'pipe', 'pipe'], env: { ...process.env, TZ: 'UTC' } });

  let playbackStarted = false;
  let stopped = false;
  let errors = '';
  const stop = async () => {
    if (stopped) return;
    stopped = true;
    if (playbackStarted) await stopGroupedPlayback(device.id, sessionId).catch(() => undefined);
    if (ffmpeg.exitCode === null) ffmpeg.kill('SIGTERM');
    await fs.rm(dir, { recursive: true, force: true }).catch(() => undefined);
  };

  ffmpeg.stderr.on('data', (chunk) => { errors = `${errors}\n${String(chunk)}`.slice(-5000); });
  res.setHeader('Content-Type', 'video/mp4');
  res.setHeader('Cache-Control', 'no-store');
  ffmpeg.stdout.pipe(res);
  res.once('close', () => { void stop(); });

  try {
    await startGroupedPlayback({
      deviceId: device.id,
      sessionId,
      sdkChannel: sdkChannel(channel),
      start,
      end,
      fifoPath
    });
    playbackStarted = true;
    await new Promise<void>((resolve, reject) => {
      ffmpeg.once('exit', (code) => {
        if (code === 0 || res.writableEnded) resolve();
        else reject(new Error(errors.trim() || `HCNetSDK grouped archive export ffmpeg exited ${code}`));
      });
      ffmpeg.once('error', reject);
    });
  } finally {
    await stop();
  }
}
