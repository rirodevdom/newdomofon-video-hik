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
        """  playbackStarted: boolean;\n  retiredAt: number | null;\n}""",
        """  playbackStarted: boolean;\n  retiredAt: number | null;\n  viewerIds: Set<string>;\n}""",
        "archive viewer ownership state",
    )

    text = replace_once(
        text,
        """  async getOrCreate(device: HikvisionDeviceConfig, channel: HikvisionChannel, start: Date, requestedEnd: Date): Promise<NativeArchiveSession> {\n    const maxEnd = new Date(start.getTime() + config.deviceArchiveSessionSeconds * 1000);""",
        """  async getOrCreate(\n    device: HikvisionDeviceConfig,\n    channel: HikvisionChannel,\n    start: Date,\n    requestedEnd: Date,\n    viewerId?: string\n  ): Promise<NativeArchiveSession> {\n    const normalizedViewerId = String(viewerId || '').trim().slice(0, 128);\n    const maxEnd = new Date(start.getTime() + config.deviceArchiveSessionSeconds * 1000);""",
        "archive viewer getOrCreate signature",
    )

    old_existing = """      if (existing) {\n        existing.lastAccessAt = Date.now();\n        return existing;\n      }\n\n      await this.ensureDeviceCapacity(device.id, id);"""
    new_existing = """      if (existing) {\n        existing.lastAccessAt = Date.now();\n        if (normalizedViewerId) {\n          existing.viewerIds.add(normalizedViewerId);\n          await this.releaseViewerFromOtherSessions(device.id, normalizedViewerId, existing.id);\n        }\n        return existing;\n      }\n\n      if (normalizedViewerId) {\n        await this.releaseViewerFromOtherSessions(device.id, normalizedViewerId, id);\n      }\n      await this.ensureDeviceCapacity(device.id, id);"""
    text = replace_once(text, old_existing, new_existing, "reuse archive viewer ownership")

    text = replace_once(
        text,
        """        playbackStarted: false,\n        retiredAt: null\n      };""",
        """        playbackStarted: false,\n        retiredAt: null,\n        viewerIds: new Set(normalizedViewerId ? [normalizedViewerId] : [])\n      };""",
        "new archive viewer ownership",
    )

    helper_anchor = """  private async ensureDeviceCapacity(deviceId: string, keepId: string): Promise<void> {"""
    helper = """  private async releaseViewerFromOtherSessions(deviceId: string, viewerId: string, keepId: string): Promise<void> {\n    if (!viewerId) return;\n    for (const session of [...this.sessions.values()]) {\n      if (session.deviceId !== deviceId || session.id === keepId || !session.viewerIds.has(viewerId)) continue;\n      session.viewerIds.delete(viewerId);\n      if (session.viewerIds.size === 0) await this.retireSession(session);\n    }\n  }\n\n  async releaseViewer(deviceId: string, viewerId: string): Promise<void> {\n    const normalizedViewerId = String(viewerId || '').trim().slice(0, 128);\n    if (!normalizedViewerId) return;\n    await this.withDeviceQueue(deviceId, async () => {\n      await this.releaseViewerFromOtherSessions(deviceId, normalizedViewerId, '');\n    });\n  }\n\n  private async ensureDeviceCapacity(deviceId: string, keepId: string): Promise<void> {"""
    text = replace_once(text, helper_anchor, helper, "archive viewer release helpers")

    path.write_text(text, encoding="utf-8")


def patch_media_routes(path: Path) -> None:
    text = path.read_text(encoding="utf-8")

    old_session = """      const session = await sessions.getOrCreate(found.device.config, found.channel, start, end);"""
    new_session = """      const viewerId = String(req.body?.viewer_id ?? req.query.viewer_id ?? '').trim();\n      if (viewerId && !/^[A-Za-z0-9._~-]{8,128}$/.test(viewerId)) {\n        throw Object.assign(new Error('Invalid archive viewer id'), { statusCode: 400 });\n      }\n      const session = await sessions.getOrCreate(found.device.config, found.channel, start, end, viewerId || undefined);"""
    text = replace_once(text, old_session, new_session, "archive session viewer id")

    release_route = r'''
  router.post('/channels/:channelId/archive/viewer/release', async (req, res) => {
    try {
      const found = find(req, service);
      authorizeMedia(req, found.channelId, 'archive');
      const viewerId = String(req.body?.viewer_id ?? req.query.viewer_id ?? '').trim();
      if (!/^[A-Za-z0-9._~-]{8,128}$/.test(viewerId)) {
        throw Object.assign(new Error('Invalid archive viewer id'), { statusCode: 400 });
      }
      if (!(sessions instanceof NativeSdkArchiveSessionManager)) {
        return res.json({ ok: true, released: false, reason: 'legacy-session-manager' });
      }
      await sessions.releaseViewer(found.device.config.id, viewerId);
      return res.json({ ok: true, released: true });
    } catch (error) {
      sendError(res, error);
    }
  });
'''
    anchor = """  router.get('/channels/:channelId/archive/sessions/:sessionId/index.m3u8', async (req, res) => {"""
    if "archive/viewer/release" not in text:
        count = text.count(anchor)
        if count != 1:
            raise SystemExit(f"archive viewer release route anchor: expected one source block, found {count}")
        text = text.replace(anchor, release_route + "\n" + anchor, 1)

    path.write_text(text, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-dir", default=".")
    args = parser.parse_args()
    root = Path(args.project_dir).resolve()
    patch_archive_sessions(root / "src/nativeSdk/archiveSessions.ts")
    patch_media_routes(root / "src/http/mediaRoutes.ts")
    print("Archive viewer leases now replace only the same viewer's previous producer")
    print("Archive viewer release endpoint prepared")


if __name__ == "__main__":
    main()
