#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

MARKER = 'SMARTYARD_VIRTUAL_ARCHIVE_PLAYBACK_FALLBACK'


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if new in text:
        return text
    count = text.count(old)
    if count != 1:
        raise SystemExit(f'{label}: expected one source block, found {count}')
    return text.replace(old, new, 1)


def patch_media_routes(path: Path) -> None:
    text = path.read_text(encoding='utf-8')
    if MARKER in text:
        print('SmartYard virtual archive grouped-playback fallback already prepared')
        return

    text = replace_once(
        text,
        "import { downloadNativeArchiveRange } from '../nativeSdk/client.js';\n",
        "import { downloadNativeArchiveRange, sdkChannel } from '../nativeSdk/client.js';\nimport { startGroupedPlayback, stopGroupedPlayback } from '../nativeSdk/deviceRuntime.js';\n",
        'virtual archive grouped playback imports',
    )

    helper_anchor = "async function ensureVirtualArchiveSegment(\n"
    helper = r'''const SMARTYARD_VIRTUAL_ARCHIVE_PLAYBACK_FALLBACK = 'grouped-playback';

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

async function renderVirtualSegmentViaGroupedPlayback(
  found: Awaited<ReturnType<typeof find>>,
  start: Date,
  duration: number,
  root: string,
  key: string,
  output: string
): Promise<void> {
  const fifoPath = path.join(root, `${key}.${Date.now()}.playback.ps.fifo`);
  const sessionId = `virtual-${safeId(found.channelId)}-${start.getTime()}-${Date.now()}`;
  const end = new Date(start.getTime() + duration * 1000 + 1000);
  let playbackStarted = false;
  await createFifo(fifoPath);
  try {
    await startGroupedPlayback({
      deviceId: found.device.config.id,
      sessionId,
      sdkChannel: sdkChannel(found.channel),
      start,
      end,
      fifoPath
    });
    playbackStarted = true;
    await runFfmpegToFile([
      '-hide_banner', '-loglevel', config.logLevel,
      '-fflags', '+genpts+discardcorrupt',
      '-probesize', '2097152', '-analyzeduration', '2000000',
      '-f', 'mpeg', '-i', fifoPath,
      '-t', String(duration),
      '-map', '0:v:0', '-map', '0:a?',
      '-c:v', 'libx264', '-preset', 'veryfast', '-tune', 'zerolatency',
      '-g', '50', '-sc_threshold', '0',
      '-c:a', 'aac', '-b:a', '64k', '-ac', '1', '-ar', '44100',
      '-avoid_negative_ts', 'make_zero',
      '-muxdelay', '0', '-muxpreload', '0',
      '-mpegts_flags', '+resend_headers',
      '-f', 'mpegts', output
    ], output);
  } finally {
    if (playbackStarted) await stopGroupedPlayback(found.device.config.id, sessionId).catch(() => undefined);
    await fs.rm(fifoPath, { force: true }).catch(() => undefined);
  }
}

'''
    text = replace_once(text, helper_anchor, helper + helper_anchor, 'virtual archive grouped playback helper')

    start_marker = '    let trimSeconds = 2;\n'
    end_marker = '    await runFfmpegToFile(ffmpegArgs, temp);\n'
    start = text.find(start_marker)
    if start < 0:
        raise SystemExit('virtual archive primary render start not found')
    end = text.find(end_marker, start)
    if end < 0:
        raise SystemExit('virtual archive primary render end not found')
    end += len(end_marker)

    replacement = r'''    let primaryError: unknown = null;
    try {
      let trimSeconds = 2;
      let sourceStart = new Date(start.getTime() - 2000);
      let sourceEnd = new Date(start.getTime() + roundedDuration * 1000 + 1000);
      try {
        await downloadNativeArchiveRange(found.device.config, found.channel, sourceStart, sourceEnd, raw);
      } catch (firstError) {
        trimSeconds = 0;
        sourceStart = start;
        sourceEnd = new Date(start.getTime() + roundedDuration * 1000);
        await fs.rm(raw, { force: true }).catch(() => undefined);
        try {
          await downloadNativeArchiveRange(found.device.config, found.channel, sourceStart, sourceEnd, raw);
        } catch {
          throw firstError;
        }
      }

      const ffmpegArgs = [
        '-hide_banner', '-loglevel', config.logLevel,
        '-fflags', '+genpts+discardcorrupt',
        '-probesize', '2097152', '-analyzeduration', '2000000',
        '-i', raw,
        ...(trimSeconds > 0 ? ['-ss', String(trimSeconds)] : []),
        '-t', String(roundedDuration),
        '-map', '0:v:0', '-map', '0:a?',
        '-c:v', 'libx264', '-preset', 'veryfast', '-tune', 'zerolatency',
        '-g', '50', '-sc_threshold', '0',
        '-c:a', 'aac', '-b:a', '64k', '-ac', '1', '-ar', '44100',
        '-avoid_negative_ts', 'make_zero',
        '-muxdelay', '0', '-muxpreload', '0',
        '-mpegts_flags', '+resend_headers',
        '-f', 'mpegts', temp
      ];
      await runFfmpegToFile(ffmpegArgs, temp);
    } catch (error) {
      primaryError = error;
      await Promise.all([
        fs.rm(raw, { force: true }).catch(() => undefined),
        fs.rm(temp, { force: true }).catch(() => undefined)
      ]);
      try {
        await renderVirtualSegmentViaGroupedPlayback(found, start, roundedDuration, root, key, temp);
      } catch (fallbackError) {
        const primaryMessage = primaryError instanceof Error ? primaryError.message : String(primaryError);
        const fallbackMessage = fallbackError instanceof Error ? fallbackError.message : String(fallbackError);
        throw new Error(`Virtual archive segment failed: download=${primaryMessage}; grouped_playback=${fallbackMessage}`);
      }
    }
'''
    text = text[:start] + replacement + text[end:]

    if MARKER not in text or 'renderVirtualSegmentViaGroupedPlayback' not in text or 'grouped_playback=' not in text:
        raise SystemExit('virtual archive grouped playback fallback markers are incomplete')
    path.write_text(text, encoding='utf-8')
    print('SmartYard virtual archive now falls back to exact-time grouped HCNetSDK playback')


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--project-dir', default='.')
    args = parser.parse_args()
    root = Path(args.project_dir).resolve()
    patch_media_routes(root / 'src/http/mediaRoutes.ts')


if __name__ == '__main__':
    main()
