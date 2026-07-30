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


def patch_device_archive(path: Path) -> None:
    text = path.read_text(encoding="utf-8")

    old = '''  const matching = items.filter((item) => new Date(item.end) >= start && new Date(item.start) <= end);
  const candidates = matching.map((item) => item.playback_uri);
  // Generic UTC playback is the preferred fallback for devices that advertise
  // isSupportPlaybackByUTC=true.
  for (const trackId of channel.archive_track_ids) candidates.push(playbackRtspFallback(device, trackId, start, end));
  // Keep the original file-bound URI last for incompatible older devices. It
  // must never win before the exact UTC candidates.
  for (const item of matching) {
    if (item.original_playback_uri) candidates.push(item.original_playback_uri);
  }
  return [...new Set(candidates)];'''

    new = '''  return orderDevicePlaybackCandidates(device, channel, items, start, end);
}

export function orderDevicePlaybackCandidates(
  device: HikvisionDeviceConfig,
  channel: HikvisionChannel,
  items: DeviceArchiveItem[],
  start: Date,
  end: Date
): string[] {
  const matching = items.filter((item) => new Date(item.end) >= start && new Date(item.start) <= end);
  const candidates: string[] = [];

  // DS-H208QA advertises isSupportPlaybackByUTC=true. Its file-bound playbackURI
  // can ignore a changed starttime while the requested point remains inside the
  // same roughly hour-long recording file. Therefore the generic UTC track URI
  // must be attempted before every URI that still identifies a recording file.
  for (const trackId of channel.archive_track_ids) {
    candidates.push(playbackRtspFallback(device, trackId, start, end));
  }

  // Search-result URIs retain useful vendor parameters and remain compatible
  // fallbacks for devices that reject generic UTC track playback.
  for (const item of matching) candidates.push(item.playback_uri);

  // The untouched file-bound URI is the final compatibility fallback only.
  for (const item of matching) {
    if (item.original_playback_uri) candidates.push(item.original_playback_uri);
  }

  return [...new Set(candidates)];'''

    text = replace_once(text, old, new, "generic UTC candidate priority")
    path.write_text(text, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-dir", default=".")
    args = parser.parse_args()
    root = Path(args.project_dir).resolve()
    patch_device_archive(root / "src/archive/deviceArchive.ts")
    print("Generic UTC archive playback is prioritized before file-bound URIs")


if __name__ == "__main__":
    main()
