#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

MARKER = 'SMARTYARD_VIRTUAL_ARCHIVE_BURST'


def patch_media_routes(path: Path) -> None:
    text = path.read_text(encoding='utf-8')
    if MARKER in text:
        print('SmartYard virtual archive burst producer already prepared')
        return

    start = text.find("const SMARTYARD_VIRTUAL_ARCHIVE_SEGMENT =")
    end = text.find("export function createMediaRouter(", start)
    if start < 0 or end < 0:
        raise SystemExit('virtual archive helper block not found')

    helpers = r'''const SMARTYARD_VIRTUAL_ARCHIVE_SEGMENT = 'smartyard-virtual-archive-segment';
const SMARTYARD_VIRTUAL_ARCHIVE_BURST = 'SMARTYARD_VIRTUAL_ARCHIVE_BURST';
const VIRTUAL_ARCHIVE_SEGMENT_MAX_SECONDS = 6;
const VIRTUAL_ARCHIVE_CACHE_MS = 15 * 60 * 1000;
const VIRTUAL_ARCHIVE_BURST_SEGMENTS = 15;
const VIRTUAL_ARCHIVE_BURST_TIMEOUT_MS = 45_000;
const VIRTUAL_ARCHIVE_SEGMENT_WAIT_MS = 9_000;

interface VirtualArchiveBurst {
  channelId: string;
  startMs: number;
  endMs: number;
  segmentSeconds: number;
  controller: AbortController;
  waiters: Set<string>;
  job: Promise<void>;
}

const virtualArchiveBursts = new Map<string, VirtualArchiveBurst>();

function virtualArchiveAbortError(): Error & { statusCode?: number } {
  return Object.assign(new Error('Virtual archive request aborted by client'), { statusCode: 503 });
}

function virtualArchiveRoot(channelId: string): string {
  return path.join(config.tempRoot, 'smartyard-virtual-archive', safeId(channelId));
}

function virtualArchiveOutputPath(
  found: Awaited<ReturnType<typeof find>>,
  start: Date,
  duration: number
): string {
  const roundedDuration = Math.max(1, Math.min(VIRTUAL_ARCHIVE_SEGMENT_MAX_SECONDS, duration));
  const root = virtualArchiveRoot(found.channelId);
  const key = `${start.getTime()}-${roundedDuration.toFixed(3)}`.replace(/[^0-9.-]+/g, '_');
  return path.join(root, `${key}.ts`);
}

async function freshVirtualSegment(file: string): Promise<boolean> {
  try {
    const stat = await fs.stat(file);
    return stat.isFile() && stat.size > 188 && Date.now() - stat.mtimeMs <= VIRTUAL_ARCHIVE_CACHE_MS;
  } catch {
    return false;
  }
}

async function createFifo(file: string): Promise<void> {
  await fs.rm(file, { force: true }).catch(() => undefined);
  const child = spawn('mkfifo', ['-m', '600', file], { stdio: ['ignore', 'ignore', 'pipe'] });
  let stderr = '';
  child.stderr?.on('data', (chunk) => { stderr = `${stderr}\n${String(chunk)}`.slice(-2000); });
  await new Promise<void>((resolve, reject) => {
    const timer = setTimeout(() => {
      child.kill('SIGKILL');
      reject(new Error('mkfifo timeout'));
    }, 5_000);
    timer.unref?.();
    child.once('error', (error) => { clearTimeout(timer); reject(error); });
    child.once('exit', (code) => {
      clearTimeout(timer);
      if (code === 0) resolve();
      else reject(new Error(stderr.trim() || `mkfifo exited ${code}`));
    });
  });
}

function delay(ms: number): Promise<void> {
  return new Promise((resolve) => {
    const timer = setTimeout(resolve, ms);
    timer.unref?.();
  });
}

async function promoteBurstParts(
  found: Awaited<ReturnType<typeof find>>,
  burstStart: Date,
  segmentSeconds: number,
  workDir: string,
  final: boolean
): Promise<void> {
  let names: string[] = [];
  try { names = await fs.readdir(workDir); } catch { return; }
  const indexes = names
    .map((name) => /^part-(\d+)\.ts$/.exec(name))
    .filter((match): match is RegExpExecArray => Boolean(match))
    .map((match) => Number(match[1]))
    .filter(Number.isInteger)
    .sort((a, b) => a - b);
  const present = new Set(indexes);

  for (const index of indexes) {
    if (!final && !present.has(index + 1)) continue;
    const source = path.join(workDir, `part-${String(index).padStart(3, '0')}.ts`);
    const segmentStart = new Date(burstStart.getTime() + index * segmentSeconds * 1000);
    const output = virtualArchiveOutputPath(found, segmentStart, segmentSeconds);
    if (await freshVirtualSegment(output)) {
      await fs.rm(source, { force: true }).catch(() => undefined);
      continue;
    }
    try {
      const stat = await fs.stat(source);
      if (!stat.isFile() || stat.size <= 188) continue;
      await fs.rm(output, { force: true }).catch(() => undefined);
      await fs.rename(source, output);
    } catch {
      // The segmenter may still be closing the newest file. A later poll retries.
    }
  }
}

async function runBurstFfmpeg(
  found: Awaited<ReturnType<typeof find>>,
  fifoPath: string,
  burstStart: Date,
  segmentSeconds: number,
  workDir: string,
  signal: AbortSignal
): Promise<void> {
  const burstSeconds = segmentSeconds * VIRTUAL_ARCHIVE_BURST_SEGMENTS;
  const pattern = path.join(workDir, 'part-%03d.ts');
  const args = [
    '-hide_banner', '-loglevel', config.logLevel,
    '-fflags', '+genpts+discardcorrupt',
    '-probesize', '2097152', '-analyzeduration', '2000000',
    '-f', 'mpeg', '-i', fifoPath,
    '-t', String(burstSeconds),
    '-map', '0:v:0', '-map', '0:a?',
    '-c:v', 'libx264', '-preset', 'veryfast', '-tune', 'zerolatency',
    '-g', '50', '-sc_threshold', '0',
    '-force_key_frames', `expr:gte(t,n_forced*${segmentSeconds})`,
    '-c:a', 'aac', '-b:a', '64k', '-ac', '1', '-ar', '44100',
    '-avoid_negative_ts', 'make_zero',
    '-f', 'segment',
    '-segment_time', String(segmentSeconds),
    '-segment_format', 'mpegts',
    '-reset_timestamps', '1',
    '-segment_start_number', '0',
    pattern
  ];

  const child = spawn(config.ffmpegPath, args, { stdio: ['ignore', 'ignore', 'pipe'] });
  let stderr = '';
  let settled = false;
  child.stderr?.on('data', (chunk) => { stderr = `${stderr}\n${String(chunk)}`.slice(-6000); });

  const done = new Promise<void>((resolve, reject) => {
    let finished = false;
    let timer: NodeJS.Timeout;
    const onAbort = () => {
      child.kill('SIGKILL');
      finish(virtualArchiveAbortError());
    };
    const finish = (error?: Error) => {
      if (finished) return;
      finished = true;
      settled = true;
      clearTimeout(timer);
      signal.removeEventListener('abort', onAbort);
      if (error) reject(error);
      else resolve();
    };
    timer = setTimeout(() => {
      child.kill('SIGKILL');
      finish(new Error('Virtual archive burst ffmpeg timeout'));
    }, VIRTUAL_ARCHIVE_BURST_TIMEOUT_MS);
    timer.unref?.();
    if (signal.aborted) onAbort();
    else signal.addEventListener('abort', onAbort, { once: true });
    child.once('error', (error) => finish(error));
    child.once('exit', (code) => {
      if (signal.aborted) finish(virtualArchiveAbortError());
      else if (code === 0) finish();
      else finish(new Error(stderr.trim() || `Virtual archive burst ffmpeg exited ${code}`));
    });
  });

  while (!settled) {
    await promoteBurstParts(found, burstStart, segmentSeconds, workDir, false);
    await delay(50);
  }
  try {
    await done;
  } finally {
    await promoteBurstParts(found, burstStart, segmentSeconds, workDir, true);
  }
}

async function runVirtualArchiveBurst(
  found: Awaited<ReturnType<typeof find>>,
  burstStart: Date,
  segmentSeconds: number,
  controller: AbortController
): Promise<void> {
  const root = virtualArchiveRoot(found.channelId);
  const burstSeconds = segmentSeconds * VIRTUAL_ARCHIVE_BURST_SEGMENTS;
  const key = `${burstStart.getTime()}-${Date.now()}`;
  const workDir = path.join(root, `.burst-${key}`);
  const fifoPath = path.join(workDir, 'playback.ps.fifo');
  const sessionId = `virtual-burst-${safeId(found.channelId)}-${burstStart.getTime()}-${Date.now()}`;
  const end = new Date(burstStart.getTime() + burstSeconds * 1000 + 1000);
  let playbackStarted = false;

  await fs.mkdir(workDir, { recursive: true, mode: 0o750 });
  await createFifo(fifoPath);
  try {
    await startGroupedPlayback({
      deviceId: found.device.config.id,
      sessionId,
      sdkChannel: sdkChannel(found.channel),
      start: burstStart,
      end,
      fifoPath,
      fastSteps: 2
    });
    playbackStarted = true;
    await runBurstFfmpeg(found, fifoPath, burstStart, segmentSeconds, workDir, controller.signal);
  } finally {
    if (playbackStarted) {
      await stopGroupedPlayback(found.device.config.id, sessionId).catch(() => undefined);
    } else if (controller.signal.aborted) {
      await stopGroupedPlayback(found.device.config.id, sessionId).catch(() => undefined);
    }
    await fs.rm(workDir, { recursive: true, force: true }).catch(() => undefined);
  }
}

function burstContains(burst: VirtualArchiveBurst, start: Date, segmentSeconds: number): boolean {
  return !burst.controller.signal.aborted
    && Math.abs(burst.segmentSeconds - segmentSeconds) < 0.001
    && start.getTime() >= burst.startMs
    && start.getTime() < burst.endMs;
}

function startVirtualArchiveBurst(
  found: Awaited<ReturnType<typeof find>>,
  start: Date,
  segmentSeconds: number
): VirtualArchiveBurst {
  const existing = virtualArchiveBursts.get(found.channelId);
  if (existing && !existing.controller.signal.aborted) existing.controller.abort();

  const controller = new AbortController();
  const burst: VirtualArchiveBurst = {
    channelId: found.channelId,
    startMs: start.getTime(),
    endMs: start.getTime() + segmentSeconds * VIRTUAL_ARCHIVE_BURST_SEGMENTS * 1000,
    segmentSeconds,
    controller,
    waiters: new Set<string>(),
    job: Promise.resolve()
  };
  burst.job = runVirtualArchiveBurst(found, start, segmentSeconds, controller).finally(() => {
    if (virtualArchiveBursts.get(found.channelId) === burst) virtualArchiveBursts.delete(found.channelId);
  });
  void burst.job.catch(() => undefined);
  virtualArchiveBursts.set(found.channelId, burst);
  return burst;
}

async function waitForBurstSegment(output: string, burst: VirtualArchiveBurst): Promise<string> {
  const deadline = Date.now() + VIRTUAL_ARCHIVE_SEGMENT_WAIT_MS;
  for (;;) {
    if (await freshVirtualSegment(output)) return output;
    if (burst.controller.signal.aborted) throw virtualArchiveAbortError();
    if (Date.now() >= deadline) {
      throw Object.assign(new Error('Virtual archive burst segment is not ready yet'), { statusCode: 503 });
    }
    const completed = await Promise.race([
      burst.job.then(() => true, (error) => { throw error; }),
      delay(50).then(() => false)
    ]);
    if (completed) {
      if (await freshVirtualSegment(output)) return output;
      throw Object.assign(new Error('Virtual archive burst completed without requested segment'), { statusCode: 404 });
    }
  }
}

function cancelVirtualArchiveSegment(
  found: Awaited<ReturnType<typeof find>>,
  start: Date,
  duration: number
): void {
  const roundedDuration = Math.max(1, Math.min(VIRTUAL_ARCHIVE_SEGMENT_MAX_SECONDS, duration));
  const output = virtualArchiveOutputPath(found, start, roundedDuration);
  const burst = virtualArchiveBursts.get(found.channelId);
  if (!burst || !burstContains(burst, start, roundedDuration)) return;
  burst.waiters.delete(output);
  if (burst.waiters.size === 0) burst.controller.abort();
}

async function ensureVirtualArchiveSegment(
  found: Awaited<ReturnType<typeof find>>,
  start: Date,
  duration: number
): Promise<string> {
  if (found.channel.archive_storage !== 'device') {
    throw Object.assign(new Error('Virtual archive segment is only used for device archive'), { statusCode: 409 });
  }
  const roundedDuration = Math.max(1, Math.min(VIRTUAL_ARCHIVE_SEGMENT_MAX_SECONDS, duration));
  const root = virtualArchiveRoot(found.channelId);
  await fs.mkdir(root, { recursive: true, mode: 0o750 });
  const output = virtualArchiveOutputPath(found, start, roundedDuration);
  if (await freshVirtualSegment(output)) return output;

  let burst = virtualArchiveBursts.get(found.channelId);
  if (!burst || !burstContains(burst, start, roundedDuration)) {
    burst = startVirtualArchiveBurst(found, start, roundedDuration);
  }

  burst.waiters.add(output);
  try {
    return await waitForBurstSegment(output, burst);
  } finally {
    burst.waiters.delete(output);
  }
}

'''

    text = text[:start] + helpers + text[end:]
    if MARKER not in text or 'VIRTUAL_ARCHIVE_BURST_SEGMENTS = 15' not in text or 'runVirtualArchiveBurst' not in text:
        raise SystemExit('virtual archive burst markers are incomplete')
    path.write_text(text, encoding='utf-8')
    print('SmartYard virtual archive now reuses one grouped playback for up to 15 four-second segments')
    print('Sequential HLS fragments are produced progressively from one minute-long HCNetSDK burst')
    print('Browser seeks cancel the old burst; continuous playback no longer START/STOPs HCNetSDK every four seconds')


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--project-dir', default='.')
    args = parser.parse_args()
    root = Path(args.project_dir).resolve()
    patch_media_routes(root / 'src/http/mediaRoutes.ts')


if __name__ == '__main__':
    main()
