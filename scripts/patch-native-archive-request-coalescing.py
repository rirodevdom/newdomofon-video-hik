#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if new in text:
        return text
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one source block, found {count}")
    return text.replace(old, new, 1)


def patch_archive_sessions(path: Path) -> None:
    text = path.read_text(encoding="utf-8")

    text = replace_once(
        text,
        """const ARCHIVE_SLOT_IDLE_MS = 30_000;
const CLEANUP_INTERVAL_MS = 10_000;""",
        """const ARCHIVE_SLOT_IDLE_MS = 30_000;
const ARCHIVE_REQUEST_COALESCE_MS = 5_000;
const CLEANUP_INTERVAL_MS = 10_000;""",
        "archive request coalesce window",
    )

    old = """      const existing = this.sessions.get(id);
      if (existing && existing.status !== 'error' && existing.status !== 'cancelled' && existing.status !== 'retired') {
        existing.lastAccessAt = Date.now();
        return existing;
      }

      await this.ensureDeviceCapacity(device.id, id);"""
    new = """      const exact = this.sessions.get(id);
      const exactReusable = exact
        && exact.status !== 'error'
        && exact.status !== 'cancelled'
        && exact.status !== 'retired'
        ? exact
        : undefined;
      const existing = exactReusable || [...this.sessions.values()].find((candidate) => (
        candidate.channelId === channel.id
        && candidate.status !== 'error'
        && candidate.status !== 'cancelled'
        && candidate.status !== 'retired'
        && Math.abs(candidate.start.getTime() - start.getTime()) <= ARCHIVE_REQUEST_COALESCE_MS
        && Math.abs(candidate.end.getTime() - end.getTime()) <= ARCHIVE_REQUEST_COALESCE_MS
      ));
      if (existing) {
        existing.lastAccessAt = Date.now();
        return existing;
      }

      await this.ensureDeviceCapacity(device.id, id);"""
    text = replace_once(text, old, new, "near-identical archive request coalescing")

    path.write_text(text, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-dir", default=".")
    args = parser.parse_args()
    root = Path(args.project_dir).resolve()
    patch_archive_sessions(root / "src/nativeSdk/archiveSessions.ts")
    print("Near-identical archive requests now share one playback producer within 5 seconds")


if __name__ == "__main__":
    main()
