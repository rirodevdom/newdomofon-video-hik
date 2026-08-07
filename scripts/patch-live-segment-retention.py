#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

MARKER = 'HIK_LIVE_DELETE_THRESHOLD'


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if new in text:
        return text
    count = text.count(old)
    if count != 1:
        raise SystemExit(f'{label}: expected one source block, found {count}')
    return text.replace(old, new, 1)


def patch_config(path: Path) -> None:
    text = path.read_text(encoding='utf-8')
    if "liveDeleteThreshold: numberEnv('HIK_LIVE_DELETE_THRESHOLD'" not in text:
        text = replace_once(
            text,
            "  segmentSeconds: numberEnv('HIK_SEGMENT_SECONDS', 4, 1),\n  liveWindow: numberEnv('HIK_LIVE_WINDOW', 8, 2),",
            "  segmentSeconds: numberEnv('HIK_SEGMENT_SECONDS', 4, 1),\n  liveWindow: numberEnv('HIK_LIVE_WINDOW', 8, 2),\n  liveDeleteThreshold: numberEnv('HIK_LIVE_DELETE_THRESHOLD', 60, 2),",
            'live HLS retention config',
        )
    path.write_text(text, encoding='utf-8')


def patch_recorder(path: Path, label: str) -> None:
    text = path.read_text(encoding='utf-8')
    if "String(config.liveDeleteThreshold)" in text:
        return
    text = replace_once(
        text,
        "...(nodeArchive ? ['-strftime', '1', '-strftime_mkdir', '1'] : ['-hls_delete_threshold', '2']),",
        "...(nodeArchive ? ['-strftime', '1', '-strftime_mkdir', '1'] : ['-hls_delete_threshold', String(config.liveDeleteThreshold)]),",
        label,
    )
    path.write_text(text, encoding='utf-8')


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--project-dir', default='.')
    args = parser.parse_args()
    root = Path(args.project_dir).resolve()

    patch_config(root / 'src/config.ts')
    patch_recorder(root / 'src/media/recorderManager.ts', 'legacy live HLS retention')
    patch_recorder(root / 'src/nativeSdk/recorderManager.ts', 'native live HLS retention')

    print('Live HLS now retains 60 unreferenced segments by default for player recovery')
    print('Override with HIK_LIVE_DELETE_THRESHOLD when a different retention is required')


if __name__ == '__main__':
    main()
