#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path


def patch_archive_sessions(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    new_marker = "'-hls_segment_type', 'fmp4'"
    if new_marker in text and "seg_%06d.m4s" in text and "'-hls_fmp4_init_filename', 'init.mp4'" in text:
        print("Native grouped archive browser-compatible fMP4 HLS already prepared")
        return

    old = """      '-f', 'hls', '-hls_time', '2', '-hls_list_size', '0',
      '-hls_flags', 'temp_file+program_date_time+independent_segments',
      '-hls_segment_filename', 'seg_%06d.ts',
      'index.m3u8'"""
    new = """      '-f', 'hls', '-hls_time', '2', '-hls_list_size', '0',
      '-hls_flags', 'temp_file+program_date_time+independent_segments',
      '-hls_segment_type', 'fmp4',
      '-hls_fmp4_init_filename', 'init.mp4',
      '-hls_segment_filename', 'seg_%06d.m4s',
      'index.m3u8'"""
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"native grouped archive HLS block: expected one source block, found {count}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")
    print("Native grouped archive now emits fMP4 HLS for browser playback")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-dir", default=".")
    args = parser.parse_args()
    root = Path(args.project_dir).resolve()
    patch_archive_sessions(root / "src/nativeSdk/archiveSessions.ts")


if __name__ == "__main__":
    main()
