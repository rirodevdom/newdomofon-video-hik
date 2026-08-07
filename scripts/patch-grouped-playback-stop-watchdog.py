#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

MARKER = 'GROUPED_PLAYBACK_STOP_WATCHDOG'
FFMPEG_MARKER = 'VIRTUAL_ARCHIVE_ABORTABLE_FFMPEG'


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if new in text:
        return text
    count = text.count(old)
    if count != 1:
        raise SystemExit(f'{label}: expected one source block, found {count}')
    return text.replace(old, new, 1)


def patch_device_runtime(path: Path) -> None:
    text = path.read_text(encoding='utf-8')
    if MARKER in text:
        print('Grouped playback stop watchdog already prepared')
        return

    old_timeout = """    const timer = setTimeout(() => {
      pendingAcks.delete(key);
      // NET_DVR_PlayBackByTime can occasionally return after our HTTP-side ACK
      // deadline. If that happens, queue a best-effort STOP behind the delayed
      // PLAYBACK command so a late native start cannot leak a playback slot.
      if (operation === 'start' && current.child.stdin?.writable) {
        const safeSessionId = safeField(sessionId, 'timeout cleanup session');
        current.child.stdin.write(`STOP_PLAYBACK\\t${safeSessionId}\\n`);
      }
      reject(Object.assign(
        new Error(`Grouped HCNetSDK playback ${operation} acknowledgement timed out for session ${sessionId}`),
        { statusCode: 503 }
      ));
    }, 10_000);"""

    new_timeout = """    const timer = setTimeout(() => {
      pendingAcks.delete(key);
      // GROUPED_PLAYBACK_STOP_WATCHDOG
      // A late PLAYBACK start can still be cleaned up by a STOP queued behind it.
      // A STOP timeout is different: the native worker has already accepted the
      // command but failed to return from NET_DVR_StopPlayBack, so its single
      // per-DVR command loop is wedged. Kill only that DVR worker; the recorder
      // manager exit handler will recreate it and its live pipelines.
      if (operation === 'start' && current.child.stdin?.writable) {
        const safeSessionId = safeField(sessionId, 'timeout cleanup session');
        current.child.stdin.write(`STOP_PLAYBACK\\t${safeSessionId}\\n`);
      } else if (operation === 'stop' && current.child.exitCode === null) {
        console.error(
          `[hcnetsdk-device:${deviceId}] grouped playback STOP acknowledgement timed out; restarting stuck DVR worker session=${sessionId}`
        );
        current.child.kill('SIGKILL');
      }
      reject(Object.assign(
        new Error(`Grouped HCNetSDK playback ${operation} acknowledgement timed out for session ${sessionId}`),
        { statusCode: 503 }
      ));
    }, 10_000);"""

    text = replace_once(text, old_timeout, new_timeout, 'grouped playback stop watchdog')
    if MARKER not in text or "current.child.kill('SIGKILL')" not in text:
        raise SystemExit('Grouped playback stop watchdog markers are incomplete')
    path.write_text(text, encoding='utf-8')
    print('Grouped playback STOP timeout now restarts only the stuck DVR worker')
    print('A hung NET_DVR_StopPlayBack can no longer wedge one DVR indefinitely')


def patch_media_routes(path: Path) -> None:
    text = path.read_text(encoding='utf-8')
    if FFMPEG_MARKER in text:
        print('Virtual archive abortable ffmpeg already prepared')
        return

    old_ffmpeg = r'''async function runFfmpegToFile(args: string[], output: string): Promise<void> {
  const child = spawn(config.ffmpegPath, args, { stdio: ['ignore', 'ignore', 'pipe'] });
  let stderr = '';
  child.stderr?.on('data', (chunk) => { stderr = `${stderr}\n${String(chunk)}`.slice(-6000); });
  await new Promise<void>((resolve, reject) => {
    const timer = setTimeout(() => {
      child.kill('SIGKILL');
      reject(new Error('Virtual archive segment ffmpeg timeout'));
    }, 20_000);
    timer.unref?.();
    child.once('error', (error) => { clearTimeout(timer); reject(error); });
    child.once('exit', (code) => {
      clearTimeout(timer);
      if (code === 0) resolve();
      else reject(new Error(stderr.trim() || `Virtual archive segment ffmpeg exited ${code}`));
    });
  });
  const stat = await fs.stat(output);
  if (!stat.isFile() || stat.size <= 188) throw new Error('Virtual archive segment is empty');
}'''

    new_ffmpeg = r'''const VIRTUAL_ARCHIVE_ABORTABLE_FFMPEG = true;
async function runFfmpegToFile(args: string[], output: string, signal?: AbortSignal): Promise<void> {
  const child = spawn(config.ffmpegPath, args, { stdio: ['ignore', 'ignore', 'pipe'] });
  let stderr = '';
  child.stderr?.on('data', (chunk) => { stderr = `${stderr}\n${String(chunk)}`.slice(-6000); });
  await new Promise<void>((resolve, reject) => {
    let settled = false;
    let timer: NodeJS.Timeout | null = null;
    const cleanup = () => {
      if (timer) clearTimeout(timer);
      timer = null;
      signal?.removeEventListener('abort', onAbort);
    };
    const finish = (error?: Error) => {
      if (settled) return;
      settled = true;
      cleanup();
      if (error) reject(error);
      else resolve();
    };
    const onAbort = () => {
      child.kill('SIGKILL');
      finish(Object.assign(new Error('Virtual archive request aborted by client'), { statusCode: 503 }));
    };

    timer = setTimeout(() => {
      child.kill('SIGKILL');
      finish(new Error('Virtual archive segment ffmpeg timeout'));
    }, 20_000);
    timer.unref?.();

    if (signal?.aborted) onAbort();
    else signal?.addEventListener('abort', onAbort, { once: true });

    child.once('error', (error) => finish(error));
    child.once('exit', (code) => {
      if (code === 0) finish();
      else finish(new Error(stderr.trim() || `Virtual archive segment ffmpeg exited ${code}`));
    });
  });
  const stat = await fs.stat(output);
  if (!stat.isFile() || stat.size <= 188) throw new Error('Virtual archive segment is empty');
}'''

    text = replace_once(text, old_ffmpeg, new_ffmpeg, 'abortable virtual archive ffmpeg')

    start = text.find('async function renderVirtualSegmentViaGroupedPlayback(')
    end = text.find('async function ensureVirtualArchiveSegment(', start)
    if start < 0 or end < 0:
        raise SystemExit('grouped virtual archive renderer not found')
    renderer = text[start:end]
    old_call = """      '-f', 'mpegts', output
    ], output);"""
    new_call = """      '-f', 'mpegts', output
    ], output, signal);"""
    if renderer.count(old_call) != 1:
        raise SystemExit(f'grouped virtual archive ffmpeg call: expected one source block, found {renderer.count(old_call)}')
    renderer = renderer.replace(old_call, new_call, 1)
    text = text[:start] + renderer + text[end:]

    if FFMPEG_MARKER not in text or '], output, signal);' not in text:
        raise SystemExit('Virtual archive abortable ffmpeg markers are incomplete')
    path.write_text(text, encoding='utf-8')
    print('Aborted SmartYard virtual segments now terminate their ffmpeg reader immediately')
    print('Grouped playback cleanup now reaches STOP without waiting for the 20 second ffmpeg timeout')


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--project-dir', default='.')
    args = parser.parse_args()
    root = Path(args.project_dir).resolve()
    patch_device_runtime(root / 'src/nativeSdk/deviceRuntime.ts')
    patch_media_routes(root / 'src/http/mediaRoutes.ts')


if __name__ == '__main__':
    main()
