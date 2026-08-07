#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

MARKER = 'NATIVE_ARCHIVE_WORKER_CAPACITY_ENV'


def patch_client(path: Path) -> None:
    text = path.read_text(encoding='utf-8')
    if MARKER in text:
        print('Native archive worker capacity env already prepared')
        return
    old = "env: { ...deviceEnv(device), HIK_SDK_DEVICE_LIVE_CONFIG: liveConfigPath, ...extra },"
    new = """env: {
      ...deviceEnv(device),
      HIK_SDK_DEVICE_LIVE_CONFIG: liveConfigPath,
      HIK_SDK_MAX_PLAYBACKS: String(config.deviceArchiveMaxActivePerDvr),
      ...extra
    }, // NATIVE_ARCHIVE_WORKER_CAPACITY_ENV"""
    count = text.count(old)
    if count != 1:
        raise SystemExit(f'archive worker capacity env: expected one source block, found {count}')
    text = text.replace(old, new, 1)
    path.write_text(text, encoding='utf-8')
    print('Dedicated live/archive workers retain the configured per-DVR native playback ceiling')


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--project-dir', default='.')
    args = parser.parse_args()
    patch_client(Path(args.project_dir).resolve() / 'src/nativeSdk/client.ts')


if __name__ == '__main__':
    main()
