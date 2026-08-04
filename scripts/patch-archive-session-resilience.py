#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

MARKER = "newdomofon-hik-archive-session-resilience"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if new in text:
        return text
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one source block, found {count}")
    return text.replace(old, new, 1)


def patch_sessions(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    if MARKER in text:
        print("Hikvision archive session resilience already prepared")
        return

    text = replace_once(
        text,
        "const CANDIDATE_STARTUP_TIMEOUT_MS = 8_000;\nconst SESSION_READY_TIMEOUT_MS = 25_000;",
        "const CANDIDATE_STARTUP_TIMEOUT_MS = 10_000;\nconst SESSION_READY_TIMEOUT_MS = 35_000;\nconst ARCHIVE_SESSION_RESILIENCE = 'newdomofon-hik-archive-session-resilience';",
        "archive session timeouts",
    )

    helper_anchor = '''async function exists(file: string): Promise<boolean> {
  try {
    await fs.access(file);
    return true;
  } catch {
    return false;
  }
}
'''
    helper = helper_anchor + '''
function archiveSessionError(message: string, statusCode = 503): Error & { statusCode: number } {
  return Object.assign(new Error(message), { statusCode });
}

async function archiveSessionPlayable(session: ArchiveSession): Promise<boolean> {
  try {
    const body = await fs.readFile(session.playlist, 'utf8');
    const mediaLine = body.split(/\\r?\\n/).map((line) => line.trim()).find((line) => line && !line.startsWith('#'));
    if (!mediaLine) return false;
    const mediaFile = mediaLine.split('?')[0]!;
    if (!await exists(path.join(session.dir, mediaFile))) return false;
    const mapMatch = body.match(/#EXT-X-MAP:URI="([^"]+)"/i);
    if (mapMatch?.[1] && !await exists(path.join(session.dir, mapMatch[1].split('?')[0]!))) return false;
    return true;
  } catch {
    return false;
  }
}
'''
    text = replace_once(text, helper_anchor, helper, "playable archive session helper")

    text = replace_once(
        text,
        '''    if (existing) {
      await this.terminateAndRemove(existing, 'Restarting inactive archive session');
    }
    await this.retireSuperseded(channel.id, id);

    const dir = path.join(config.tempRoot, 'device-archive', safeId(channel.id), id);''',
        '''    if (existing) {
      await this.terminateAndRemove(existing, 'Restarting inactive archive session');
    }

    // Keep the current playable session alive while a replacement is prepared.
    // Browsers may still be reading its playlist/segments and a failed new RTSP
    // attempt must never destroy the last known-good archive session.
    const dir = path.join(config.tempRoot, 'device-archive', safeId(channel.id), id);''',
        "defer superseded retirement",
    )

    text = replace_once(
        text,
        '''    this.sessions.set(id, session);
    await this.spawn(device, channel, session);
    await this.waitReady(session);
    return session;''',
        '''    this.sessions.set(id, session);
    try {
      await this.spawn(device, channel, session);
      await this.waitReady(session);
      await this.retireSuperseded(channel.id, id);
      return session;
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      await this.terminateAndRemove(session, `Archive session preparation failed: ${message}`);
      if (error && typeof error === 'object' && 'statusCode' in error) throw error;
      throw archiveSessionError(message || 'Hikvision archive session preparation failed', 502);
    }''',
        "atomic archive session replacement",
    )

    text = text.replace("if (await exists(session.playlist)) {", "if (await archiveSessionPlayable(session)) {")
    text = text.replace("if (session.cancelled || await exists(session.playlist)) return;", "if (session.cancelled || await archiveSessionPlayable(session)) return;")

    text = replace_once(
        text,
        "    if (!candidates.length) throw new Error('No device archive playback candidates');",
        "    if (!candidates.length) throw archiveSessionError('No device archive playback candidates', 404);",
        "archive candidate error status",
    )
    text = replace_once(
        text,
        "        throw new Error(session.error || 'Archive session was superseded by a newer seek');",
        "        throw archiveSessionError(session.error || 'Archive session was superseded by a newer seek', 409);",
        "superseded session status",
    )
    text = replace_once(
        text,
        "      if (session.status === 'error') throw new Error(session.error || 'Device archive session failed');",
        "      if (session.status === 'error') throw archiveSessionError(session.error || 'Device archive session failed', 502);",
        "archive upstream failure status",
    )
    text = replace_once(
        text,
        "    throw new Error('Device archive session did not produce a playlist in time');",
        "    throw archiveSessionError('Device archive session did not produce a playable HLS segment in time', 503);",
        "archive readiness timeout status",
    )

    if text.count("archiveSessionPlayable(session)") < 3:
        raise SystemExit("archive playable readiness checks were not installed everywhere")
    if MARKER not in text:
        raise SystemExit("archive resilience marker missing")

    path.write_text(text, encoding="utf-8")
    print("Hikvision archive sessions now replace atomically after a playable segment exists")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-dir", default=".")
    args = parser.parse_args()
    patch_sessions(Path(args.project_dir).resolve() / "src/archive/deviceArchiveSessions.ts")


if __name__ == "__main__":
    main()
