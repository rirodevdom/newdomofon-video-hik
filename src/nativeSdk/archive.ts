import { spawn } from 'node:child_process';
import type { Response } from 'express';
import { config } from '../config.js';
import type { ArchiveRange, HikvisionChannel, HikvisionDeviceConfig } from '../types.js';
import { findNativeArchive, spawnNativeStream } from './client.js';

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
  const worker = spawnNativeStream(device, channel, 'playback', {
    HIK_SDK_START: start.toISOString(),
    HIK_SDK_END: end.toISOString(),
    HIK_SDK_STREAM_TYPE: '0'
  });
  const ffmpeg = spawn(config.ffmpegPath, [
    '-hide_banner', '-loglevel', config.logLevel,
    '-fflags', '+genpts+discardcorrupt',
    '-probesize', '2097152', '-analyzeduration', '2000000',
    '-f', 'mpeg', '-i', 'pipe:0',
    '-t', String(duration),
    '-map', '0:v:0', '-map', '0:a?',
    '-c:v', 'libx264', '-preset', 'veryfast',
    '-c:a', 'aac', '-b:a', '64k',
    '-movflags', 'frag_keyframe+empty_moov+default_base_moof',
    '-f', 'mp4', 'pipe:1'
  ], { stdio: ['pipe', 'pipe', 'pipe'] });
  worker.stdout.pipe(ffmpeg.stdin!);
  res.setHeader('Content-Type', 'video/mp4');
  res.setHeader('Cache-Control', 'no-store');
  ffmpeg.stdout.pipe(res);

  let errors = '';
  worker.stderr.on('data', (chunk) => { errors = `${errors}\n${String(chunk)}`.slice(-5000); });
  ffmpeg.stderr.on('data', (chunk) => { errors = `${errors}\n${String(chunk)}`.slice(-5000); });
  const stop = () => {
    worker.kill('SIGTERM');
    ffmpeg.kill('SIGTERM');
  };
  res.once('close', stop);
  await new Promise<void>((resolve, reject) => {
    ffmpeg.once('exit', (code) => {
      worker.kill('SIGTERM');
      if (code === 0 || res.writableEnded) resolve();
      else reject(new Error(errors.trim() || `HCNetSDK archive export ffmpeg exited ${code}`));
    });
    worker.once('error', reject);
    ffmpeg.once('error', reject);
  });
}
