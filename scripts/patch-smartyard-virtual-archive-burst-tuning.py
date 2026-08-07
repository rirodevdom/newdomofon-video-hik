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
    path.write_text(text, encoding='utf-8')
    print('SmartYard virtual archive burst timeout raised to 60 seconds for slower DVR delivery')
    print('Background burst ffmpeg failures are observed immediately while segments are promoted')


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--project-dir', default='.')
    args = parser.parse_args()
    patch_media_routes(Path(args.project_dir).resolve() / 'src/http/mediaRoutes.ts')


if __name__ == '__main__':
    main()
