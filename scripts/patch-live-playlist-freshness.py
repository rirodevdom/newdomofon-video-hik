#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

MEDIA_MARKER = 'const LIVE_PLAYLIST_STALE_MS = 15_000;'
HEALTH_MARKER = 'Date.now() - stat.mtimeMs <= 15_000'


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if new in text:
        return text
    count = text.count(old)
    if count != 1:
        raise SystemExit(f'{label}: expected one source block, found {count}')
    return text.replace(old, new, 1)


def patch_media_routes(path: Path) -> None:
    text = path.read_text(encoding='utf-8')
    if MEDIA_MARKER not in text:
        text = replace_once(
            text,
            'const LIVE_PLAYLIST_READY_TIMEOUT_MS = 20_000;\n',
            'const LIVE_PLAYLIST_READY_TIMEOUT_MS = 20_000;\nconst LIVE_PLAYLIST_STALE_MS = 15_000;\n',
            'live playlist freshness constant',
        )
    text = replace_once(
        text,
        '      if (stat.isFile() && stat.size > 0 && (!startedMs || stat.mtimeMs >= startedMs - 1000)) return;',
        '      if (stat.isFile() && stat.size > 0 && (!startedMs || stat.mtimeMs >= startedMs - 1000) && Date.now() - stat.mtimeMs <= LIVE_PLAYLIST_STALE_MS) return;',
        'live playlist freshness readiness',
    )
    path.write_text(text, encoding='utf-8')


def patch_index(path: Path) -> None:
    text = path.read_text(encoding='utf-8')
    text = replace_once(
        text,
        "        return stat.isFile() && stat.size > 0 && stat.mtimeMs >= new Date(status.started_at).getTime() - 1000;",
        "        return stat.isFile() && stat.size > 0 && stat.mtimeMs >= new Date(status.started_at).getTime() - 1000 && Date.now() - stat.mtimeMs <= 15_000;",
        'health live playlist freshness',
    )
    path.write_text(text, encoding='utf-8')


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--project-dir', default='.')
    args = parser.parse_args()
    root = Path(args.project_dir).resolve()
    patch_media_routes(root / 'src/http/mediaRoutes.ts')
    patch_index(root / 'src/index.ts')
    print('Hikvision live playlist freshness checks prepared')


if __name__ == '__main__':
    main()
