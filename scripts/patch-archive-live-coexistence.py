#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

MARKER = 'HIK_SDK_ARCHIVE_PAUSE_LIVE'


def patch_worker(path: Path) -> None:
    text = path.read_text(encoding='utf-8')
    if MARKER in text:
        print('HCNetSDK archive/live coexistence already prepared')
        return

    old = '''  sink->pausedLive = find_live(liveSinks, sdkChannel);\n\n  if (sink->pausedLive && sink->pausedLive->handle >= 0) {\n    stop_live(*sink->pausedLive, "archive_playback");\n  }'''
    new = '''  LiveSink* matchingLive = find_live(liveSinks, sdkChannel);\n  const bool pauseLiveForArchive = env_int("HIK_SDK_ARCHIVE_PAUSE_LIVE", 0) != 0;\n  sink->pausedLive = pauseLiveForArchive ? matchingLive : nullptr;\n\n  if (sink->pausedLive && sink->pausedLive->handle >= 0) {\n    stop_live(*sink->pausedLive, "archive_playback_compat");\n  } else if (matchingLive && matchingLive->handle >= 0) {\n    std::cerr << "HCNetSDK grouped archive keeps live active sdk=" << sdkChannel << "\\n";\n  }'''

    count = text.count(old)
    if count != 1:
        raise SystemExit(f'archive/live coexistence: expected one source block, found {count}')
    text = text.replace(old, new, 1)
    path.write_text(text, encoding='utf-8')
    print('HCNetSDK archive playback now keeps same-channel live active by default')
    print('Set HIK_SDK_ARCHIVE_PAUSE_LIVE=1 only for DVRs that require legacy pause behavior')


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--project-dir', default='.')
    args = parser.parse_args()
    root = Path(args.project_dir).resolve()
    patch_worker(root / 'native-sdk/hik_sdk_device_worker.cpp')


if __name__ == '__main__':
    main()
