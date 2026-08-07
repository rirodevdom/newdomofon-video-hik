#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

MARKER = 'SMARTYARD_VIRTUAL_ARCHIVE_REQUEST_ABORT'


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
        print('SmartYard virtual archive request-abort cancellation already prepared')
        return

    text = replace_once(
        text,
        "const virtualArchiveJobs = new Map<string, Promise<string>>();",
        "const virtualArchiveJobs = new Map<string, Promise<string>>();\nconst virtualArchiveCancels = new Map<string, () => void>();\nconst SMARTYARD_VIRTUAL_ARCHIVE_REQUEST_ABORT = 'SMARTYARD_VIRTUAL_ARCHIVE_REQUEST_ABORT';",
        'virtual archive cancel registry',
    )

    helper_anchor = "async function renderVirtualSegmentViaGroupedPlayback(\n"
    helpers = r'''function virtualArchiveOutputPath(
  found: Awaited<ReturnType<typeof find>>,
  start: Date,
  duration: number
): string {
  const roundedDuration = Math.max(1, Math.min(VIRTUAL_ARCHIVE_SEGMENT_MAX_SECONDS, duration));
  const root = path.join(config.tempRoot, 'smartyard-virtual-archive', safeId(found.channelId));
  const key = `${start.getTime()}-${roundedDuration.toFixed(3)}`.replace(/[^0-9.-]+/g, '_');
  return path.join(root, `${key}.ts`);
}

function cancelVirtualArchiveSegment(
  found: Awaited<ReturnType<typeof find>>,
  start: Date,
  duration: number
): void {
  virtualArchiveCancels.get(virtualArchiveOutputPath(found, start, duration))?.();
}

function virtualArchiveAbortError(): Error & { statusCode?: number } {
  return Object.assign(new Error('Virtual archive request aborted by client'), { statusCode: 503 });
}

async function waitForVirtualArchiveStart(
  promise: Promise<void>,
  signal?: AbortSignal
): Promise<void> {
  if (!signal) return promise;
  if (signal.aborted) throw virtualArchiveAbortError();
  let onAbort!: () => void;
  const aborted = new Promise<void>((_, reject) => {
    onAbort = () => reject(virtualArchiveAbortError());
    signal.addEventListener('abort', onAbort, { once: true });
  });
  try {
    await Promise.race([promise, aborted]);
  } finally {
    signal.removeEventListener('abort', onAbort);
  }
}

'''
    text = replace_once(text, helper_anchor, helpers + helper_anchor, 'virtual archive abort helpers')

    text = replace_once(
        text,
        """  key: string,
  output: string
): Promise<void> {""",
        """  key: string,
  output: string,
  signal?: AbortSignal
): Promise<void> {""",
        'grouped virtual archive abort signal input',
    )

    old_start = """    await startGroupedPlayback({
      deviceId: found.device.config.id,
      sessionId,
      sdkChannel: sdkChannel(found.channel),
      start,
      end,
      fifoPath,
      fastSteps: 2
    });
    playbackStarted = true;"""
    new_start = """    await waitForVirtualArchiveStart(startGroupedPlayback({
      deviceId: found.device.config.id,
      sessionId,
      sdkChannel: sdkChannel(found.channel),
      start,
      end,
      fifoPath,
      fastSteps: 2
    }), signal);
    playbackStarted = true;"""
    text = replace_once(text, old_start, new_start, 'abortable grouped playback start')

    old_finally = """  } finally {
    if (playbackStarted) await stopGroupedPlayback(found.device.config.id, sessionId).catch(() => undefined);
    await fs.rm(fifoPath, { force: true }).catch(() => undefined);
  }
}"""
    new_finally = """  } catch (error) {
    if (signal?.aborted && !playbackStarted) {
      // If NET_DVR_PlayBackByTime finishes after the HTTP request was already
      // cancelled, queue a STOP behind the in-flight start so the late native
      // playback cannot remain resident in the grouped worker.
      await stopGroupedPlayback(found.device.config.id, sessionId).catch(() => undefined);
    }
    throw error;
  } finally {
    if (playbackStarted) await stopGroupedPlayback(found.device.config.id, sessionId).catch(() => undefined);
    await fs.rm(fifoPath, { force: true }).catch(() => undefined);
  }
}"""
    text = replace_once(text, old_finally, new_finally, 'grouped playback abort cleanup')

    old_job_prefix = """  const job = (async () => {
    await fs.mkdir(root, { recursive: true, mode: 0o750 });"""
    new_job_prefix = """  const controller = new AbortController();
  virtualArchiveCancels.set(output, () => controller.abort());

  const job = (async () => {
    await fs.mkdir(root, { recursive: true, mode: 0o750 });"""
    text = replace_once(text, old_job_prefix, new_job_prefix, 'virtual archive job abort controller')

    text = replace_once(
        text,
        "await renderVirtualSegmentViaGroupedPlayback(found, start, roundedDuration, root, key, temp);",
        "await renderVirtualSegmentViaGroupedPlayback(found, start, roundedDuration, root, key, temp, controller.signal);",
        'pass abort signal to grouped renderer',
    )

    old_grouped_catch = """      const GROUPED_PLAYBACK_ACK_TIMEOUT_FAST_FAIL = true;
      if (groupedError instanceof Error && groupedError.message.includes('acknowledgement timed out')) {
        throw Object.assign(new Error(groupedError.message), { statusCode: 503 });
      }
      try {"""
    new_grouped_catch = """      const GROUPED_PLAYBACK_ACK_TIMEOUT_FAST_FAIL = true;
      if (controller.signal.aborted) throw virtualArchiveAbortError();
      if (groupedError instanceof Error && groupedError.message.includes('acknowledgement timed out')) {
        throw Object.assign(new Error(groupedError.message), { statusCode: 503 });
      }
      try {"""
    text = replace_once(text, old_grouped_catch, new_grouped_catch, 'skip fallback after request abort')

    old_job_finally = """  })().finally(() => virtualArchiveJobs.delete(output));

  virtualArchiveJobs.set(output, job);"""
    new_job_finally = """  })().finally(() => {
    virtualArchiveJobs.delete(output);
    virtualArchiveCancels.delete(output);
  });

  virtualArchiveJobs.set(output, job);"""
    text = replace_once(text, old_job_finally, new_job_finally, 'virtual archive cancel registry cleanup')

    old_route = """      const file = await ensureVirtualArchiveSegment(found, start, duration);
      res.setHeader('X-Newdomofon-Hikvision-Archive-Mode', SMARTYARD_VIRTUAL_ARCHIVE_SEGMENT);
      await serveFile(res, file);"""
    new_route = """      let requestAborted = false;
      const cancel = () => {
        requestAborted = true;
        cancelVirtualArchiveSegment(found, start, duration);
      };
      const onClose = () => {
        if (!res.writableEnded) cancel();
      };
      req.once('aborted', cancel);
      res.once('close', onClose);
      try {
        const file = await ensureVirtualArchiveSegment(found, start, duration);
        if (requestAborted || req.destroyed || res.destroyed) return;
        res.setHeader('X-Newdomofon-Hikvision-Archive-Mode', SMARTYARD_VIRTUAL_ARCHIVE_SEGMENT);
        await serveFile(res, file);
      } finally {
        req.off('aborted', cancel);
        res.off('close', onClose);
      }"""
    text = replace_once(text, old_route, new_route, 'cancel virtual producer on HTTP abort')

    if MARKER not in text or 'virtualArchiveCancels' not in text or 'req.once(\'aborted\'' not in text:
        raise SystemExit('SmartYard virtual archive abort markers are incomplete')

    path.write_text(text, encoding='utf-8')
    print('SmartYard virtual archive producers now stop when their HTTP request is aborted')
    print('Aborted seek jobs no longer continue into grouped playback/download fallback')


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--project-dir', default='.')
    args = parser.parse_args()
    root = Path(args.project_dir).resolve()
    patch_media_routes(root / 'src/http/mediaRoutes.ts')


if __name__ == '__main__':
    main()
