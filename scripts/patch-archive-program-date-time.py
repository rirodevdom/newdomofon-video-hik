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


def patch_media_routes(path: Path) -> None:
    text = path.read_text(encoding="utf-8")

    old_serve = '''async function servePlaylist(res: Response, file: string, token: string): Promise<void> {
  const body = await fs.readFile(file, 'utf8');
  const rewritten = body.split(/\\r?\\n/).map((line) => appendToken(line, token)).join('\\n');
  res.setHeader('Content-Type', 'application/vnd.apple.mpegurl');
  res.setHeader('Cache-Control', 'no-store');
  res.send(rewritten);
}'''
    new_serve = '''function rewriteArchiveProgramDateTime(body: string, archiveStart: Date): string {
  let cursorMs = archiveStart.getTime();
  let segmentDurationMs = 0;

  return body.split(/\\r?\\n/).map((line) => {
    const trimmed = line.trim();
    if (trimmed.startsWith('#EXTINF:')) {
      const duration = Number(trimmed.slice('#EXTINF:'.length).split(',')[0]);
      segmentDurationMs = Number.isFinite(duration) && duration > 0 ? duration * 1000 : 0;
      return line;
    }
    if (trimmed.startsWith('#EXT-X-PROGRAM-DATE-TIME:')) {
      return `#EXT-X-PROGRAM-DATE-TIME:${new Date(cursorMs).toISOString()}`;
    }
    if (trimmed && !trimmed.startsWith('#')) {
      cursorMs += segmentDurationMs;
      segmentDurationMs = 0;
    }
    return line;
  }).join('\\n');
}

async function servePlaylist(
  res: Response,
  file: string,
  token: string,
  archiveStart?: Date
): Promise<void> {
  const body = await fs.readFile(file, 'utf8');
  const timed = archiveStart ? rewriteArchiveProgramDateTime(body, archiveStart) : body;
  const rewritten = timed.split(/\\r?\\n/).map((line) => appendToken(line, token)).join('\\n');
  res.setHeader('Content-Type', 'application/vnd.apple.mpegurl');
  res.setHeader('Cache-Control', 'no-store');
  res.send(rewritten);
}'''
    text = replace_once(text, old_serve, new_serve, "archive program date time rewrite")

    old_session = "      await servePlaylist(res, session.playlist, token);"
    new_session = "      await servePlaylist(res, session.playlist, token, session.start);"
    text = replace_once(text, old_session, new_session, "archive session start time")

    path.write_text(text, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-dir", default=".")
    args = parser.parse_args()
    root = Path(args.project_dir).resolve()
    patch_media_routes(root / "src/http/mediaRoutes.ts")
    print("Device archive playlists now use the requested recording time")


if __name__ == "__main__":
    main()
