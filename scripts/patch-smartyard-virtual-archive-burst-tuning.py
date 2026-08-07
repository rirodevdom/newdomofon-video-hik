#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

MARKER = 'SMARTYARD_VIRTUAL_ARCHIVE_BURST_TUNING'


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
        print('SmartYard virtual archive burst production tuning already prepared')
        return
    text = replace_once(
        text,
        "const VIRTUAL_ARCHIVE_BURST_TIMEOUT_MS = 45_000;",
        "const VIRTUAL_ARCHIVE_BURST_TIMEOUT_MS = 60_000;\nconst SMARTYARD_VIRTUAL_ARCHIVE_BURST_TUNING = true;",
        'virtual archive burst timeout headroom',
    )
    text = replace_once(
        text,
        """  while (!settled) {
    await promoteBurstParts(found, burstStart, segmentSeconds, workDir, false);""",
        """  // The producer runs independently from the HTTP waiter. Attach a rejection
  // observer immediately so an abort/ffmpeg failure cannot become an unhandled
  // rejection during the short interval before the polling loop reaches await done.
  void done.catch(() => undefined);

  while (!settled) {
    await promoteBurstParts(found, burstStart, segmentSeconds, workDir, false);""",
        'virtual archive burst rejection observer',
    )

    # Earlier media patches already materialize a generic delay() helper. Keep
    # every helper introduced by this late burst block namespaced so the final
    # TypeScript cannot collide with preceding materialization steps.
    burst_start = text.find("const SMARTYARD_VIRTUAL_ARCHIVE_BURST =")
    burst_end = text.find("export function createMediaRouter(", burst_start)
    if burst_start < 0 or burst_end < 0:
        raise SystemExit('virtual archive burst helper block not found for tuning')
    block = text[burst_start:burst_end]
    if block.count('function delay(ms: number)') != 1:
        raise SystemExit(f'virtual archive burst delay helper: expected one source block, found {block.count("function delay(ms: number)")}')
    block = block.replace('function delay(ms: number)', 'function virtualArchiveDelay(ms: number)', 1)
    block = block.replace('delay(50)', 'virtualArchiveDelay(50)')
    text = text[:burst_start] + block + text[burst_end:]

    path.write_text(text, encoding='utf-8')
    print('SmartYard virtual archive burst timeout raised to 60 seconds for slower DVR delivery')
    print('Background burst ffmpeg failures are observed immediately while segments are promoted')
    print('SmartYard burst helpers are namespaced to avoid collisions with earlier media patches')


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--project-dir', default='.')
    args = parser.parse_args()
    patch_media_routes(Path(args.project_dir).resolve() / 'src/http/mediaRoutes.ts')


if __name__ == '__main__':
    main()
