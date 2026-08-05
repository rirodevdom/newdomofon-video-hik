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
    old = """      const retiredExpired = session.status === 'retired' && session.retiredAt !== null && now - session.retiredAt >= RETIRED_GRACE_MS;"""
    new = """      const retiredExpired = session.status === 'retired'
        && session.retiredAt !== null
        && now - session.retiredAt >= RETIRED_GRACE_MS
        && now - session.lastAccessAt >= RETIRED_GRACE_MS;"""
    text = replace_once(text, old, new, "retired archive access grace")
    path.write_text(text, encoding="utf-8")


def patch_media_routes(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    old = """function appendToken(line: string, token: string): string {
  if (!token || !line || line.startsWith('#')) return line;
  const separator = line.includes('?') ? '&' : '?';
  return `${line}${separator}token=${encodeURIComponent(token)}`;
}"""
    new = """function appendTokenToUri(uri: string, token: string): string {
  if (!token || !uri) return uri;
  const separator = uri.includes('?') ? '&' : '?';
  return `${uri}${separator}token=${encodeURIComponent(token)}`;
}

function appendToken(line: string, token: string): string {
  if (!token || !line) return line;
  if (line.startsWith('#')) {
    return line.replace(/URI=\"([^\"]+)\"/g, (_match, uri: string) => `URI=\"${appendTokenToUri(uri, token)}\"`);
  }
  return appendTokenToUri(line, token);
}"""
    text = replace_once(text, old, new, "HLS URI attribute token propagation")
    path.write_text(text, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-dir", default=".")
    args = parser.parse_args()
    root = Path(args.project_dir).resolve()
    patch_archive_sessions(root / "src/nativeSdk/archiveSessions.ts")
    patch_media_routes(root / "src/http/mediaRoutes.ts")
    print("Retired archive sessions now remain readable while clients are still accessing them")
    print("HLS URI attributes now receive the node media token")


if __name__ == "__main__":
    main()
