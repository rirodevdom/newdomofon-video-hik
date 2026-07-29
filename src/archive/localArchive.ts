import { spawn } from 'node:child_process';
import fs from 'node:fs/promises';
import path from 'node:path';
import type { Response } from 'express';
import { config } from '../config.js';
import type { ArchiveRange, HikvisionChannel } from '../types.js';
import { archiveDir } from '../media/paths.js';

interface Segment {
  file: string;
  start: Date;
  end: Date;
}

function parseSegmentTime(file: string): Date | null {
  const match = path.basename(file).match(/^(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})\.ts$/);
  if (!match) return null;
  const [, year, month, day, hour, minute, second] = match;
  const date = new Date(Date.UTC(Number(year), Number(month) - 1, Number(day), Number(hour), Number(minute), Number(second)));
  return Number.isFinite(date.getTime()) ? date : null;
}

async function walk(dir: string): Promise<string[]> {
  const result: string[] = [];
  let entries: Array<import('node:fs').Dirent>;
  try {
    entries = await fs.readdir(dir, { withFileTypes: true });
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === 'ENOENT') return result;
    throw error;
  }
  for (const entry of entries) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) result.push(...await walk(full));
    else if (entry.isFile() && entry.name.endsWith('.ts')) result.push(full);
  }
  return result;
}

export async function listLocalSegments(channel: HikvisionChannel): Promise<Segment[]> {
  const files = await walk(archiveDir(channel.id));
  return files.map((file) => {
    const start = parseSegmentTime(file);
    if (!start) return null;
    return {
      file,
      start,
      end: new Date(start.getTime() + config.segmentSeconds * 1000)
    };
  }).filter((item): item is Segment => Boolean(item)).sort((a, b) => a.start.getTime() - b.start.getTime());
}

export async function localArchiveRanges(channel: HikvisionChannel, start?: Date, end?: Date): Promise<ArchiveRange[]> {
  const segments = (await listLocalSegments(channel)).filter((segment) => {
    if (start && segment.end < start) return false;
    if (end && segment.start > end) return false;
    return true;
  });
  const ranges: ArchiveRange[] = [];
  const maxGapMs = Math.max(config.segmentSeconds * 2, 10) * 1000;
  for (const segment of segments) {
    const current = ranges[ranges.length - 1];
    if (!current || segment.start.getTime() - new Date(current.end).getTime() > maxGapMs) {
      ranges.push({ start: segment.start.toISOString(), end: segment.end.toISOString(), source: 'node' });
    } else if (segment.end > new Date(current.end)) {
      current.end = segment.end.toISOString();
    }
  }
  return ranges;
}

export async function streamLocalArchiveMp4(channel: HikvisionChannel, start: Date, end: Date, res: Response): Promise<void> {
  const segments = (await listLocalSegments(channel)).filter((segment) => segment.end >= start && segment.start <= end);
  if (!segments.length) {
    res.status(404).json({ error: 'No local archive segments in selected range' });
    return;
  }

  await fs.mkdir(config.tempRoot, { recursive: true, mode: 0o750 });
  const listFile = path.join(config.tempRoot, `concat-${process.pid}-${Date.now()}.txt`);
  const concat = segments.map((segment) => `file '${segment.file.replaceAll("'", "'\\''")}'`).join('\n');
  await fs.writeFile(listFile, `${concat}\n`, { mode: 0o600 });

  res.status(200);
  res.setHeader('Content-Type', 'video/mp4');
  res.setHeader('Cache-Control', 'no-store');
  const ffmpeg = spawn(config.ffmpegPath, [
    '-hide_banner',
    '-loglevel', config.logLevel,
    '-f', 'concat',
    '-safe', '0',
    '-i', listFile,
    '-map', '0:v:0',
    '-map', '0:a?',
    '-c:v', 'copy',
    '-c:a', 'aac',
    '-movflags', 'frag_keyframe+empty_moov+default_base_moof',
    '-f', 'mp4',
    'pipe:1'
  ], { stdio: ['ignore', 'pipe', 'pipe'] });

  let stderr = '';
  ffmpeg.stderr.on('data', (chunk) => { stderr = `${stderr}\n${String(chunk)}`.slice(-4000); });
  ffmpeg.stdout.pipe(res);
  const cleanup = async () => {
    ffmpeg.kill('SIGTERM');
    await fs.rm(listFile, { force: true }).catch(() => undefined);
  };
  res.once('close', () => { void cleanup(); });
  ffmpeg.once('exit', (code) => {
    void fs.rm(listFile, { force: true });
    if (code && !res.headersSent) res.status(502).json({ error: stderr || `ffmpeg exited ${code}` });
    else if (!res.writableEnded) res.end();
  });
}

export async function enforceLocalRetention(channel: HikvisionChannel): Promise<number> {
  if (channel.archive_storage !== 'node') return 0;
  const threshold = Date.now() - channel.retention_days * 24 * 60 * 60 * 1000;
  const segments = await listLocalSegments(channel);
  let deleted = 0;
  for (const segment of segments) {
    if (segment.end.getTime() >= threshold) continue;
    await fs.rm(segment.file, { force: true });
    deleted += 1;
  }
  return deleted;
}
