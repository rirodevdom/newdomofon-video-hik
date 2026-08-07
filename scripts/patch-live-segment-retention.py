#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path


def patch_config(path: Path) -> None:
    text = path.read_text(encoding='utf-8')
    marker = "liveDeleteThreshold: numberEnv('HIK_LIVE_DELETE_THRESHOLD'"
    if marker not in text:
        candidates = [
            "  liveWindow: numberEnv('HIK_LIVE_WINDOW', 6, 2),",
            "  liveWindow: numberEnv('HIK_LIVE_WINDOW', 8, 2),",
        ]
        matches = [candidate for candidate in candidates if candidate in text]
        if len(matches) != 1:
            raise SystemExit(f'live HLS retention config: expected one liveWindow anchor, found {len(matches)}')
        anchor = matches[0]
        text = text.replace(
            anchor,
            anchor + "\n  liveDeleteThreshold: numberEnv('HIK_LIVE_DELETE_THRESHOLD', 60, 2),",
            1,
        )
    path.write_text(text, encoding='utf-8')


def patch_recorder(path: Path, label: str) -> None:
    text = path.read_text(encoding='utf-8')
    if "String(config.liveDeleteThreshold)" in text:
        return

    candidates = [
        "['-hls_delete_threshold', '2']",
        "['-hls_delete_threshold', String(Math.max(4, config.liveWindow))]",
    ]
    matches = [candidate for candidate in candidates if candidate in text]
    if len(matches) != 1:
        raise SystemExit(f'{label}: expected one delete-threshold anchor, found {len(matches)}')
    text = text.replace(matches[0], "['-hls_delete_threshold', String(config.liveDeleteThreshold)]", 1)
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
