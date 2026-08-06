#!/usr/bin/env python3
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

BACKFILL_MARKER = "newdomofon-hik-native-event-backfill"


def fully_materialized(root: Path) -> bool:
    collector = (root / 'src/nativeSdk/eventCollector.ts').read_text(encoding='utf-8')
    config = (root / 'src/config.ts').read_text(encoding='utf-8')
    store = (root / 'src/events/eventStore.ts').read_text(encoding='utf-8')
    worker = (root / 'native-sdk/hik_sdk_device_worker.cpp').read_text(encoding='utf-8')
    return all([
        BACKFILL_MARKER in collector,
        'eventBackfillEnabled' in config,
        'event_backfill_state' in store,
        'EVENT_SCAN' in worker,
        'scan_historical_events' in worker,
    ])


def run_patch(root: Path, script: str) -> None:
    subprocess.run(
        [sys.executable, str(root / 'scripts' / script), '--project-dir', str(root)],
        check=True,
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--project-dir', default='.')
    args = parser.parse_args()
    root = Path(args.project_dir).resolve()

    if fully_materialized(root):
        print('Native HCNetSDK event reconciliation + persistent backfill already prepared')
        return

    run_patch(root, 'patch-native-event-reconciliation.py')
    run_patch(root, 'patch-native-event-backfill.py')

    if not fully_materialized(root):
        raise SystemExit('Native event patch stack did not fully materialize')
    print('Native HCNetSDK event stack prepared: realtime reconciliation + persistent backfill')


if __name__ == '__main__':
    main()
