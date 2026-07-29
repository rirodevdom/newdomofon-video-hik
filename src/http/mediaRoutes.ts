import { spawn } from 'node:child_process';
import fs from 'node:fs/promises';
import path from 'node:path';
import { Router, type Request, type Response } from 'express';
import { config } from '../config.js';
import type { HikvisionNodeService } from '../service.js';
import { archiveDir, liveDir, safeId } from '../media/paths.js';
import { authorizeMedia, playlistMediaToken } from './mediaToken.js';
import { parseDateQuery, sendError } from './helpers.js';
import { localArchiveRanges, streamLocalArchiveMp4 } from '../archive/localArchive.js';
import { searchDeviceArchive, streamDeviceArchiveMp4 } from '../archive/deviceArchive.js';
import { DeviceArchiveSessionManager } from '../archive/deviceArchiveSessions.js';

function liveRoot(channelId: string, archiveStorage: 'node' | 'device'): string {
  return archiveStorage === 'node' ? archiveDir(channelId) : liveDir(channelId);
}

function safeFile(root: string, relative: string): string {
  const normalized = relative.replace(/^\/+/, '');
  if (normalized.includes('\0')) throw Object.assign(new Error('Invalid media path'), { statusCode: 400 });
  const resolvedRoot = path.resolve(root);
  const resolved = path.resolve(root, normalized);
  if (resolved !== resolvedRoot && !resolved.startsWith(`${resolvedRoot}${path.sep}`)) {
    throw Object.assign(new Error('Invalid media path'), { statusCode: 400 });
  }
  return resolved;
}

function appendToken(line: string, token: string): string {
  if (!token || !line || line.startsWith('#')) return line;
  const separator = line.includes('?') ? '&' : '?';
  return `${line}${separator}token=${encodeURIComponent(token)}`;
}

async function servePlaylist(res: Response, file: string, token: string): Promise<void> {
  const body = await fs.readFile(file, 'utf8');
  const rewritten = body.split(/\r?\n/).map((line) => appendToken(line, token)).join('\n');
  res.setHeader('Content-Type', 'application/vnd.apple.mpegurl');
  res.setHeader('Cache-Control', 'no-store');
  res.send(rewritten);
}

async function serveFile(res: Response, file: string): Promise<void> {
  const stat = await fs.stat(file);
  if (!stat.isFile()) throw Object.assign(new Error('Media file not found'), { statusCode: 404 });
  if (file.endsWith('.ts')) res.setHeader('Content-Type', 'video/mp2t');
  else if (file.endsWith('.m4s')) res.setHeader('Content-Type', 'video/iso.segment');
  else if (file.endsWith('.mp4')) res.setHeader('Content-Type', 'video/mp4');
  res.setHeader('Content-Length', String(stat.size));
  res.setHeader('Cache-Control', 'private, max-age=30');
  res.sendFile(file);
}

function find(req: Request, service: HikvisionNodeService) {
  const channelId = decodeURIComponent(String(req.params.channelId || ''));
  const found = service.findChannel(channelId);
  if (!found) throw Object.assign(new Error('Hikvision channel not found'), { statusCode: 404 });
  return { channelId, ...found };
}

export function createMediaRouter(service: HikvisionNodeService, sessions: DeviceArchiveSessionManager): Router {
  const router = Router();

  router.get('/channels/:channelId/live/index.m3u8', async (req, res) => {
    try {
      const found = find(req, service);
      const token = playlistMediaToken(req, found.channelId, 'live');
      await servePlaylist(res, path.join(liveRoot(found.channelId, found.channel.archive_storage), 'live.m3u8'), token);
    } catch (error) {
      sendError(res, error);
    }
  });

  router.get(/^\/channels\/([^/]+)\/live\/(.+)$/, async (req, res) => {
    try {
      const channelId = decodeURIComponent(String(req.params[0] || ''));
      const relative = decodeURIComponent(String(req.params[1] || ''));
      const found = service.findChannel(channelId);
      if (!found) throw Object.assign(new Error('Hikvision channel not found'), { statusCode: 404 });
      authorizeMedia(req, channelId, 'live');
      await serveFile(res, safeFile(liveRoot(channelId, found.channel.archive_storage), relative));
    } catch (error) {
      sendError(res, error);
    }
  });

  router.get('/channels/:channelId/snapshot.jpg', async (req, res) => {
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
  });

  router.get('/channels/:channelId/archive/ranges', async (req, res) => {
    try {
      const found = find(req, service);
      authorizeMedia(req, found.channelId, 'archive');
      const start = req.query.start ? parseDateQuery(req.query.start, 'start') : new Date(Date.now() - 24 * 60 * 60 * 1000);
      const end = req.query.end ? parseDateQuery(req.query.end, 'end') : new Date();
      if (end <= start) throw Object.assign(new Error('end must be after start'), { statusCode: 400 });
      if (found.channel.archive_storage === 'node') {
        return res.json({ source: 'node', ranges: await localArchiveRanges(found.channel, start, end) });
      }
      const items = await searchDeviceArchive(found.device.config, found.channel, start, end);
      return res.json({ source: 'device', ranges: items.map(({ start: itemStart, end: itemEnd, source }) => ({ start: itemStart, end: itemEnd, source })) });
    } catch (error) {
      sendError(res, error);
    }
  });

  router.get('/channels/:channelId/archive/export.mp4', async (req, res) => {
    try {
      const found = find(req, service);
      authorizeMedia(req, found.channelId, 'archive');
      const start = parseDateQuery(req.query.start, 'start');
      const end = parseDateQuery(req.query.end, 'end');
      if (end <= start) throw Object.assign(new Error('end must be after start'), { statusCode: 400 });
      if ((end.getTime() - start.getTime()) / 1000 > config.deviceArchiveMaxSeconds) {
        throw Object.assign(new Error(`Requested range exceeds ${config.deviceArchiveMaxSeconds} seconds`), { statusCode: 400 });
      }
      if (found.channel.archive_storage === 'node') {
        await streamLocalArchiveMp4(found.channel, start, end, res);
      } else {
        await streamDeviceArchiveMp4(found.device.config, found.channel, start, end, res);
      }
    } catch (error) {
      if (!res.headersSent) sendError(res, error);
      else res.end();
    }
  });

  router.post('/channels/:channelId/archive/session', async (req, res) => {
    try {
      const found = find(req, service);
      const token = playlistMediaToken(req, found.channelId, 'archive');
      if (found.channel.archive_storage !== 'device') {
        throw Object.assign(new Error('Archive sessions are only required for device archive'), { statusCode: 409 });
      }
      const start = parseDateQuery(req.body?.start ?? req.query.start, 'start');
      const end = parseDateQuery(req.body?.end ?? req.query.end, 'end');
      if (end <= start) throw Object.assign(new Error('end must be after start'), { statusCode: 400 });
      const session = await sessions.getOrCreate(found.device.config, found.channel, start, end);
      res.json({
        id: session.id,
        channel_id: found.channelId,
        start: session.start.toISOString(),
        end: session.end.toISOString(),
        playlist_url: `/api/v1/media/channels/${encodeURIComponent(found.channelId)}/archive/sessions/${session.id}/index.m3u8${token ? `?token=${encodeURIComponent(token)}` : ''}`
      });
    } catch (error) {
      sendError(res, error);
    }
  });

  router.get('/channels/:channelId/archive/sessions/:sessionId/index.m3u8', async (req, res) => {
    try {
      const found = find(req, service);
      const token = playlistMediaToken(req, found.channelId, 'archive');
      const session = sessions.get(found.channelId, req.params.sessionId!);
      if (!session) throw Object.assign(new Error('Device archive session not found'), { statusCode: 404 });
      await servePlaylist(res, session.playlist, token);
    } catch (error) {
      sendError(res, error);
    }
  });

  router.get('/channels/:channelId/archive/sessions/:sessionId/:segment', async (req, res) => {
    try {
      const found = find(req, service);
      authorizeMedia(req, found.channelId, 'archive');
      const session = sessions.get(found.channelId, req.params.sessionId!);
      if (!session) throw Object.assign(new Error('Device archive session not found'), { statusCode: 404 });
      const segment = safeId(req.params.segment!);
      await serveFile(res, safeFile(session.dir, segment));
    } catch (error) {
      sendError(res, error);
    }
  });

  return router;
}
