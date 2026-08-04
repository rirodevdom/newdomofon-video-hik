import { Router, type Request, type Response } from 'express';
import type { HikvisionNodeService } from '../service.js';
import { authorizeMedia } from './mediaToken.js';
import {
  getHikvisionEventStoreHealth,
  listHikvisionEvents,
  summarizeHikvisionEvents
} from '../events/eventStore.js';

function parseRange(req: Request, res: Response): { start: Date; end: Date } | null {
  const start = new Date(String(req.query.start || ''));
  const end = new Date(String(req.query.end || ''));
  if (!Number.isFinite(start.getTime()) || !Number.isFinite(end.getTime()) || end <= start) {
    res.status(400).json({ error: 'Invalid start/end' });
    return null;
  }
  const maxSeconds = Math.max(60, Number(process.env.HIK_EVENT_QUERY_MAX_SECONDS || 31 * 24 * 60 * 60));
  if ((end.getTime() - start.getTime()) / 1000 > maxSeconds) {
    res.status(413).json({ error: `Requested event range is too large. Max ${maxSeconds} seconds.` });
    return null;
  }
  return { start, end };
}

function ensureChannel(service: HikvisionNodeService, channelId: string) {
  const found = service.findChannel(channelId);
  if (!found) throw Object.assign(new Error('Hikvision channel not found'), { statusCode: 404 });
  return found;
}

export function createEventRouter(service: HikvisionNodeService): Router {
  const router = Router();

  router.get('/channels/:channelId/health', (req, res) => {
    try {
      const channelId = decodeURIComponent(String(req.params.channelId || ''));
      ensureChannel(service, channelId);
      authorizeMedia(req, channelId, 'events');
      res.setHeader('cache-control', 'no-store');
      res.json(getHikvisionEventStoreHealth());
    } catch (error) {
      const status = Number((error as any)?.statusCode || 500);
      res.status(status).json({ error: error instanceof Error ? error.message : String(error) });
    }
  });

  router.get('/channels/:channelId/summary', (req, res) => {
    try {
      const channelId = decodeURIComponent(String(req.params.channelId || ''));
      ensureChannel(service, channelId);
      authorizeMedia(req, channelId, 'events');
      const range = parseRange(req, res);
      if (!range) return;
      res.setHeader('cache-control', 'no-store');
      res.json({ items: summarizeHikvisionEvents({ channelId, start: range.start, end: range.end }) });
    } catch (error) {
      const status = Number((error as any)?.statusCode || 500);
      res.status(status).json({ error: error instanceof Error ? error.message : String(error) });
    }
  });

  router.get('/channels/:channelId', (req, res) => {
    try {
      const channelId = decodeURIComponent(String(req.params.channelId || ''));
      ensureChannel(service, channelId);
      authorizeMedia(req, channelId, 'events');
      const range = parseRange(req, res);
      if (!range) return;
      const rawLimit = Number(req.query.limit || 5000);
      const limit = Number.isFinite(rawLimit) ? Math.max(1, Math.min(5000, Math.trunc(rawLimit))) : 5000;
      const type = String(req.query.type || '').trim() || undefined;
      res.setHeader('cache-control', 'no-store');
      res.json({
        items: listHikvisionEvents({ channelId, start: range.start, end: range.end, type, limit })
      });
    } catch (error) {
      const status = Number((error as any)?.statusCode || 500);
      res.status(status).json({ error: error instanceof Error ? error.message : String(error) });
    }
  });

  return router;
}
