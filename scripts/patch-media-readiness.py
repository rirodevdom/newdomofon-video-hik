#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path


READINESS_MARKER = "const CHANNEL_READY_TIMEOUT_MS = 20_000;"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if new in text:
        return text
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one source block, found {count}")
    return text.replace(old, new, 1)


def patch_media_routes(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    if READINESS_MARKER not in text:
        text = replace_once(
            text,
            "import { DeviceArchiveSessionManager } from '../archive/deviceArchiveSessions.js';\n",
            "import { DeviceArchiveSessionManager } from '../archive/deviceArchiveSessions.js';\n\nconst CHANNEL_READY_TIMEOUT_MS = 20_000;\nconst LIVE_PLAYLIST_READY_TIMEOUT_MS = 20_000;\n\nfunction delay(ms: number): Promise<void> {\n  return new Promise((resolve) => setTimeout(resolve, ms));\n}\n",
            "media readiness constants",
        )

    old_find = '''function find(req: Request, service: HikvisionNodeService) {
  const channelId = decodeURIComponent(String(req.params.channelId || ''));
  const found = service.findChannel(channelId);
  if (!found) throw Object.assign(new Error('Hikvision channel not found'), { statusCode: 404 });
  return { channelId, ...found };
}'''
    new_find = '''async function find(req: Request, service: HikvisionNodeService) {
  const channelId = decodeURIComponent(String(req.params.channelId || ''));
  const deadline = Date.now() + CHANNEL_READY_TIMEOUT_MS;
  for (;;) {
    const found = service.findChannel(channelId);
    if (found) return { channelId, ...found };
    if (Date.now() >= deadline) {
      throw Object.assign(new Error('Hikvision channel is not ready'), { statusCode: 503 });
    }
    await delay(250);
  }
}

async function waitForLivePlaylist(
  service: HikvisionNodeService,
  channelId: string,
  file: string
): Promise<void> {
  const deadline = Date.now() + LIVE_PLAYLIST_READY_TIMEOUT_MS;
  for (;;) {
    try {
      const stat = await fs.stat(file);
      const startedAt = service.recorderManager.status(channelId).started_at;
      const startedMs = startedAt ? new Date(startedAt).getTime() : 0;
      if (stat.isFile() && stat.size > 0 && (!startedMs || stat.mtimeMs >= startedMs - 1000)) return;
    } catch {
      // Recorder can be running before its first playlist is materialized.
    }
    if (Date.now() >= deadline) {
      throw Object.assign(new Error('Hikvision live playlist is not ready'), { statusCode: 503 });
    }
    await delay(250);
  }
}'''
    text = replace_once(text, old_find, new_find, "wait for channel readiness")
    text = text.replace("const found = find(req, service);", "const found = await find(req, service);")

    old_live = "      const token = playlistMediaToken(req, found.channelId, 'live');\n      await servePlaylist(res, path.join(liveRoot(found.channelId, found.channel.archive_storage), 'live.m3u8'), token);"
    new_live = "      const token = playlistMediaToken(req, found.channelId, 'live');\n      const playlist = path.join(liveRoot(found.channelId, found.channel.archive_storage), 'live.m3u8');\n      await waitForLivePlaylist(service, found.channelId, playlist);\n      await servePlaylist(res, playlist, token);"
    text = replace_once(text, old_live, new_live, "wait for live playlist")

    if text.count(READINESS_MARKER) != 1:
        raise SystemExit(f"media readiness marker must occur once, found {text.count(READINESS_MARKER)}")
    if text.count("function delay(ms: number)") != 1:
        raise SystemExit("media readiness delay helper must occur once")
    path.write_text(text, encoding="utf-8")


def patch_index(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    if "import { statSync } from 'node:fs';" not in text:
        text = replace_once(
            text,
            "import fs from 'node:fs/promises';\n",
            "import fs from 'node:fs/promises';\nimport { statSync } from 'node:fs';\nimport path from 'node:path';\n",
            "health filesystem imports",
        )
    if "import { archiveDir, liveDir } from './media/paths.js';" not in text:
        text = replace_once(
            text,
            "import { startMasterAgent } from './master/nodeClient.js';\n",
            "import { startMasterAgent } from './master/nodeClient.js';\nimport { archiveDir, liveDir } from './media/paths.js';\n",
            "health media path imports",
        )

    old_health = '''  app.get('/health', (_req, res) => {
    res.json({
      ok: true,
      service: 'newdomofon-video-hik',
      version: '0.3.0',
      devices: service.listDevices().length,
      channels: service.allChannels().length,
      recorders: service.recorderManager.allStatuses().filter((item) => item.running).length,'''
    new_health = '''  app.get('/health', (_req, res) => {
    const channels = service.allChannels();
    const statuses = new Map(service.recorderManager.allStatuses().map((item) => [item.channel_id, item]));
    const liveExpected = channels.filter((item) => item.channel.enabled && item.channel.online !== false).length;
    const liveReady = channels.filter((item) => {
      const status = statuses.get(item.channel.id);
      if (!status?.running || !status.started_at) return false;
      const root = item.channel.archive_storage === 'node' ? archiveDir(item.channel.id) : liveDir(item.channel.id);
      try {
        const stat = statSync(path.join(root, 'live.m3u8'));
        return stat.isFile() && stat.size > 0 && stat.mtimeMs >= new Date(status.started_at).getTime() - 1000;
      } catch {
        return false;
      }
    }).length;
    res.json({
      ok: true,
      service: 'newdomofon-video-hik',
      version: '0.3.1',
      devices: service.listDevices().length,
      channels: channels.length,
      recorders: service.recorderManager.allStatuses().filter((item) => item.running).length,
      live_expected: liveExpected,
      live_ready: liveReady,'''
    text = replace_once(text, old_health, new_health, "playlist-backed health readiness")
    path.write_text(text, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-dir", default=".")
    args = parser.parse_args()
    root = Path(args.project_dir).resolve()
    patch_media_routes(root / "src/http/mediaRoutes.ts")
    patch_index(root / "src/index.ts")
    print("Hikvision media routes wait for channel and playlist readiness")


if __name__ == "__main__":
    main()
