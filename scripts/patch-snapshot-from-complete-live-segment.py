#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

MARKER = "newdomofon-hik-snapshot-complete-live-segment-v1"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if new in text:
        return text
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one source fragment, found {count}")
    return text.replace(old, new, 1)


def patch_media_routes(path: Path) -> bool:
    text = path.read_text(encoding="utf-8")
    if MARKER in text:
        return False

    helper_anchor = """function find(req: Request, service: HikvisionNodeService) {"""
    helper = """// newdomofon-hik-snapshot-complete-live-segment-v1
const SNAPSHOT_RENDER_TIMEOUT_MS = 8_000;
const SNAPSHOT_MAX_BYTES = 8 * 1024 * 1024;

async function latestCompleteLiveSegment(
  channelId: string,
  archiveStorage: 'node' | 'device'
): Promise<string> {
  const root = liveRoot(channelId, archiveStorage);
  const playlist = path.join(root, 'live.m3u8');
  const body = await fs.readFile(playlist, 'utf8');
  const candidates = body
    .split(/\\r?\\n/)
    .map((line) => line.trim())
    .filter((line) => Boolean(line) && !line.startsWith('#'))
    .reverse();

  for (const candidate of candidates) {
    const encodedRelative = candidate.split('?', 1)[0] || '';
    if (!encodedRelative || encodedRelative.startsWith('/') || /^[a-z][a-z0-9+.-]*:\\/\\//i.test(encodedRelative)) continue;

    let relative: string;
    try {
      relative = decodeURIComponent(encodedRelative);
    } catch {
      continue;
    }

    // Live-only device segments are physically stored under root/segments while
    // FFmpeg writes basename-only URIs into live.m3u8. Node-archive playlists
    // already carry their relative directory path and continue to resolve from root.
    const mediaRoot = archiveStorage === 'device' && !relative.includes('/')
      ? path.join(root, 'segments')
      : root;
    const file = safeFile(mediaRoot, relative);
    try {
      const stat = await fs.stat(file);
      if (stat.isFile() && stat.size > 0) return file;
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code !== 'ENOENT') throw error;
    }
  }

  throw Object.assign(new Error('No completed live segment is available for snapshot'), { statusCode: 503 });
}

async function renderSnapshotFromSegment(segment: string): Promise<Buffer> {
  return await new Promise<Buffer>((resolve, reject) => {
    const child = spawn(config.ffmpegPath, [
      '-hide_banner', '-loglevel', config.logLevel, '-nostdin',
      '-analyzeduration', '500000', '-probesize', '500000',
      '-i', segment,
      '-map', '0:v:0', '-an', '-frames:v', '1',
      '-q:v', '3', '-f', 'image2pipe', 'pipe:1'
    ], { stdio: ['ignore', 'pipe', 'pipe'] });

    const chunks: Buffer[] = [];
    let total = 0;
    let stderr = '';
    let forcedError: Error | null = null;
    let timedOut = false;

    const timer = setTimeout(() => {
      timedOut = true;
      child.kill('SIGKILL');
    }, SNAPSHOT_RENDER_TIMEOUT_MS);

    child.stdout.on('data', (chunk: Buffer) => {
      total += chunk.length;
      if (total > SNAPSHOT_MAX_BYTES) {
        forcedError = Object.assign(new Error('Snapshot output exceeds safety limit'), { statusCode: 502 });
        child.kill('SIGKILL');
        return;
      }
      chunks.push(Buffer.from(chunk));
    });
    child.stderr.on('data', (chunk: Buffer) => {
      if (stderr.length < 4000) stderr += String(chunk);
    });
    child.once('error', (error) => {
      clearTimeout(timer);
      reject(Object.assign(error, { statusCode: 502 }));
    });
    child.once('close', (code) => {
      clearTimeout(timer);
      if (forcedError) return reject(forcedError);
      if (timedOut) {
        return reject(Object.assign(new Error('Snapshot ffmpeg timed out'), { statusCode: 504 }));
      }
      if (code !== 0 || total === 0) {
        return reject(Object.assign(
          new Error(`Snapshot ffmpeg exited ${code}: ${stderr.trim().slice(0, 1000)}`),
          { statusCode: 502 }
        ));
      }
      resolve(Buffer.concat(chunks, total));
    });
  });
}

function find(req: Request, service: HikvisionNodeService) {"""
    text = replace_once(text, helper_anchor, helper, "snapshot helper insertion")

    old_route = """  router.get('/channels/:channelId/snapshot.jpg', async (req, res) => {
    try {
      const found = find(req, service);
      authorizeMedia(req, found.channelId, 'snapshot');
      const playlist = path.join(liveRoot(found.channelId, found.channel.archive_storage), 'live.m3u8');
      await fs.access(playlist);
      res.setHeader('Content-Type', 'image/jpeg');
      res.setHeader('Cache-Control', 'no-store');
      const child = spawn(config.ffmpegPath, [
        '-hide_banner', '-loglevel', config.logLevel,
        '-i', playlist,
        '-frames:v', '1',
        '-q:v', '3',
        '-f', 'image2pipe',
        'pipe:1'
      ], { stdio: ['ignore', 'pipe', 'pipe'] });
      child.stdout.pipe(res);
      child.once('exit', (code) => {
        if (code && !res.headersSent) res.status(502).json({ error: `Snapshot ffmpeg exited ${code}` });
        else if (!res.writableEnded) res.end();
      });
      res.once('close', () => child.kill('SIGTERM'));
    } catch (error) {
      sendError(res, error);
    }
  });"""
    new_route = """  router.get('/channels/:channelId/snapshot.jpg', async (req, res) => {
    try {
      const found = find(req, service);
      authorizeMedia(req, found.channelId, 'snapshot');
      const segment = await latestCompleteLiveSegment(found.channelId, found.channel.archive_storage);
      const jpeg = await renderSnapshotFromSegment(segment);
      res.setHeader('Content-Type', 'image/jpeg');
      res.setHeader('Cache-Control', 'no-store');
      res.setHeader('Content-Length', String(jpeg.length));
      res.send(jpeg);
    } catch (error) {
      sendError(res, error);
    }
  });"""
    text = replace_once(text, old_route, new_route, "snapshot route")

    for required in (
        MARKER,
        "latestCompleteLiveSegment(found.channelId, found.channel.archive_storage)",
        "path.join(root, 'segments')",
        "SNAPSHOT_RENDER_TIMEOUT_MS",
        "'-i', segment",
    ):
        if required not in text:
            raise RuntimeError(f"snapshot patch marker missing: {required}")

    path.write_text(text, encoding="utf-8")
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-dir", default=".")
    args = parser.parse_args()
    root = Path(args.project_dir).resolve()
    target = root / "src/http/mediaRoutes.ts"
    if not target.is_file():
        raise SystemExit(f"Target not found: {target}")
    changed = patch_media_routes(target)
    print("Hikvision snapshot from completed live segment prepared")
    print("  changed: src/http/mediaRoutes.ts" if changed else "  already up to date")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
