#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

MARKER = 'SMARTYARD_VIRTUAL_ARCHIVE_MULTIVIEWER'


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if new in text:
        return text
    count = text.count(old)
    if count != 1:
        raise SystemExit(f'{label}: expected one source block, found {count}')
    return text.replace(old, new, 1)


def replace_function(text: str, name: str, replacement: str, next_name: str) -> str:
    start = text.find(f'function {name}(')
    if start < 0:
        start = text.find(f'async function {name}(')
    end = text.find(f'function {next_name}(', start)
    if end < 0:
        end = text.find(f'async function {next_name}(', start)
    if start < 0 or end < 0:
        raise SystemExit(f'{name}: function bounds not found')
    return text[:start] + replacement.rstrip() + '\n\n' + text[end:]


def patch_media_routes(path: Path) -> None:
    text = path.read_text(encoding='utf-8')
    if MARKER in text:
        print('SmartYard virtual archive multiviewer pool already prepared')
        return

    text = replace_once(
        text,
        """const VIRTUAL_ARCHIVE_SEGMENT_WAIT_MS = 9_000;

interface VirtualArchiveBurst {""",
        """const VIRTUAL_ARCHIVE_SEGMENT_WAIT_MS = 9_000;
const VIRTUAL_ARCHIVE_MAX_BURSTS_PER_DEVICE = 2;
const SMARTYARD_VIRTUAL_ARCHIVE_MULTIVIEWER = 'SMARTYARD_VIRTUAL_ARCHIVE_MULTIVIEWER';

interface VirtualArchiveBurst {""",
        'multiviewer constants',
    )
    text = replace_once(
        text,
        """interface VirtualArchiveBurst {
  channelId: string;
  startMs: number;""",
        """interface VirtualArchiveBurst {
  channelId: string;
  deviceId: string;
  startMs: number;""",
        'burst device identity',
    )
    text = replace_once(
        text,
        "const virtualArchiveBursts = new Map<string, VirtualArchiveBurst>();",
        "const virtualArchiveBursts = new Map<string, VirtualArchiveBurst[]>();",
        'multiviewer burst map',
    )

    helper_anchor = 'function burstContains(burst: VirtualArchiveBurst, start: Date, segmentSeconds: number): boolean {'
    helpers = r'''function channelVirtualArchiveBursts(channelId: string): VirtualArchiveBurst[] {
  return virtualArchiveBursts.get(channelId) || [];
}

function activeVirtualArchiveBurstCount(deviceId: string): number {
  let count = 0;
  for (const bursts of virtualArchiveBursts.values()) {
    count += bursts.filter((burst) => burst.deviceId === deviceId && !burst.controller.signal.aborted).length;
  }
  return count;
}

function findVirtualArchiveBurst(channelId: string, start: Date, segmentSeconds: number): VirtualArchiveBurst | undefined {
  return channelVirtualArchiveBursts(channelId).find((burst) => burstContains(burst, start, segmentSeconds));
}

'''
    if helper_anchor not in text:
        raise SystemExit('burstContains anchor not found')
    text = text.replace(helper_anchor, helpers + helper_anchor, 1)

    replacement_start = r'''function startVirtualArchiveBurst(
  found: Awaited<ReturnType<typeof find>>,
  start: Date,
  segmentSeconds: number
): VirtualArchiveBurst {
  const deviceId = found.device.config.id;
  if (activeVirtualArchiveBurstCount(deviceId) >= VIRTUAL_ARCHIVE_MAX_BURSTS_PER_DEVICE) {
    throw Object.assign(
      new Error(`SmartYard archive viewer capacity reached for DVR ${deviceId}`),
      { statusCode: 429 }
    );
  }

  const controller = new AbortController();
  const burst: VirtualArchiveBurst = {
    channelId: found.channelId,
    deviceId,
    startMs: start.getTime(),
    endMs: start.getTime() + segmentSeconds * VIRTUAL_ARCHIVE_BURST_SEGMENTS * 1000,
    segmentSeconds,
    controller,
    waiters: new Set<string>(),
    job: Promise.resolve()
  };
  const bursts = channelVirtualArchiveBursts(found.channelId);
  bursts.push(burst);
  virtualArchiveBursts.set(found.channelId, bursts);
  burst.job = runVirtualArchiveBurst(found, start, segmentSeconds, controller).finally(() => {
    const current = channelVirtualArchiveBursts(found.channelId).filter((item) => item !== burst);
    if (current.length) virtualArchiveBursts.set(found.channelId, current);
    else virtualArchiveBursts.delete(found.channelId);
  });
  void burst.job.catch(() => undefined);
  return burst;
}'''
    text = replace_function(text, 'startVirtualArchiveBurst', replacement_start, 'waitForBurstSegment')

    replacement_cancel = r'''function cancelVirtualArchiveSegment(
  found: Awaited<ReturnType<typeof find>>,
  start: Date,
  duration: number
): void {
  const roundedDuration = Math.max(1, Math.min(VIRTUAL_ARCHIVE_SEGMENT_MAX_SECONDS, duration));
  const output = virtualArchiveOutputPath(found, start, roundedDuration);
  const burst = findVirtualArchiveBurst(found.channelId, start, roundedDuration);
  if (!burst) return;
  burst.waiters.delete(output);
  if (burst.waiters.size === 0) burst.controller.abort();
}'''
    text = replace_function(text, 'cancelVirtualArchiveSegment', replacement_cancel, 'ensureVirtualArchiveSegment')

    ensure_start = text.find('async function ensureVirtualArchiveSegment(')
    ensure_end = text.find('\n\nexport function createMediaRouter(', ensure_start)
    if ensure_start < 0 or ensure_end < 0:
        raise SystemExit('ensureVirtualArchiveSegment bounds not found')
    ensure = text[ensure_start:ensure_end]
    old_lookup = """  let burst = virtualArchiveBursts.get(found.channelId);
  if (!burst || !burstContains(burst, start, roundedDuration)) {
    burst = startVirtualArchiveBurst(found, start, roundedDuration);
  }"""
    new_lookup = """  let burst = findVirtualArchiveBurst(found.channelId, start, roundedDuration);
  if (!burst) {
    burst = startVirtualArchiveBurst(found, start, roundedDuration);
  }"""
    if old_lookup not in ensure:
        raise SystemExit('single-burst ensure lookup not found')
    ensure = ensure.replace(old_lookup, new_lookup, 1)
    text = text[:ensure_start] + ensure + text[ensure_end:]

    if MARKER not in text or 'VIRTUAL_ARCHIVE_MAX_BURSTS_PER_DEVICE = 2' not in text or 'Map<string, VirtualArchiveBurst[]>' not in text:
        raise SystemExit('SmartYard archive multiviewer markers incomplete')
    path.write_text(text, encoding='utf-8')
    print('SmartYard archive now supports two independent burst windows per DVR')
    print('A second viewer no longer aborts the first viewer burst')
    print('Additional concurrent SmartYard archive windows fail safely with HTTP 429')


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--project-dir', default='.')
    args = parser.parse_args()
    patch_media_routes(Path(args.project_dir).resolve() / 'src/http/mediaRoutes.ts')


if __name__ == '__main__':
    main()
