import { Router } from 'express';
import { z } from 'zod';
import type { HikvisionNodeService } from '../service.js';
import { config } from '../config.js';
import { requireControlAuth } from './controlAuth.js';
import { createMediaToken } from './mediaToken.js';
import { sendError } from './helpers.js';

const deviceSchema = z.object({
  id: z.string().min(1).max(120).regex(/^[A-Za-z0-9._:-]+$/),
  name: z.string().min(1).max(200),
  host: z.string().min(1).max(255),
  scheme: z.enum(['http', 'https']).default('http'),
  isapi_port: z.number().int().min(1).max(65535).default(80),
  rtsp_port: z.number().int().min(1).max(65535).default(554),
  username: z.string().min(1).max(200),
  password: z.string().max(500),
  archive_storage: z.enum(['node', 'device']),
  retention_days: z.number().int().min(1).max(3650).default(30),
  enabled: z.boolean().default(true),
  reject_unauthorized_tls: z.boolean().default(true),
  channel_overrides: z.record(z.object({
    enabled: z.boolean().optional(),
    archive_storage: z.enum(['node', 'device']).optional(),
    retention_days: z.number().int().min(1).max(3650).optional(),
    primary_stream_id: z.string().regex(/^\d{3,5}$/).optional()
  })).optional()
});

const mediaTokenSchema = z.object({
  channel_id: z.string().min(1),
  scopes: z.array(z.enum(['live', 'archive', 'snapshot'])).min(1),
  ttl_seconds: z.number().int().min(1).optional().default(300)
});

export function createControlRouter(service: HikvisionNodeService): Router {
  const router = Router();
  router.use(requireControlAuth);

  router.get('/devices', (_req, res) => {
    res.json({ items: service.listDevices(true) });
  });

  router.get('/devices/:deviceId', (req, res) => {
    const item = service.getDevice(req.params.deviceId!, true);
    if (!item) return res.status(404).json({ error: 'Hikvision device not found' });
    return res.json(item);
  });

  router.put('/devices/:deviceId', async (req, res) => {
    try {
      const input = deviceSchema.parse({ ...req.body, id: req.params.deviceId });
      const item = await service.upsertDevice(input);
      res.status(200).json(item);
    } catch (error) {
      sendError(res, error);
    }
  });

  router.delete('/devices/:deviceId', async (req, res) => {
    try {
      const removed = await service.removeDevice(req.params.deviceId!);
      if (!removed) return res.status(404).json({ error: 'Hikvision device not found' });
      return res.status(204).end();
    } catch (error) {
      sendError(res, error);
    }
  });

  router.post('/devices/:deviceId/sync', async (req, res) => {
    try {
      res.json(await service.syncDevice(req.params.deviceId!));
    } catch (error) {
      sendError(res, error);
    }
  });

  router.get('/channels', (_req, res) => {
    res.json({ items: service.allChannels() });
  });

  router.get('/channels/:channelId', (req, res) => {
    const found = service.findChannel(req.params.channelId!);
    if (!found) return res.status(404).json({ error: 'Hikvision channel not found' });
    return res.json({
      device: service.getDevice(found.device.config.id, true),
      channel: found.channel,
      recorder: service.recorderManager.status(found.channel.id)
    });
  });

  router.post('/channels/:channelId/streams/:streamId/refresh', async (req, res) => {
    try {
      res.json(await service.refreshStreamSettings(req.params.channelId!, req.params.streamId!));
    } catch (error) {
      sendError(res, error);
    }
  });

  router.get('/recorders', (_req, res) => {
    res.json({ items: service.recorderManager.allStatuses() });
  });

  router.post('/media-token', (req, res) => {
    try {
      const input = mediaTokenSchema.parse(req.body || {});
      if (!service.findChannel(input.channel_id)) return res.status(404).json({ error: 'Hikvision channel not found' });
      const ttl = Math.min(input.ttl_seconds, config.mediaTokenMaxSeconds);
      const token = createMediaToken(input.channel_id, input.scopes, ttl);
      return res.json({ token, expires_in: ttl, channel_id: input.channel_id, scopes: input.scopes });
    } catch (error) {
      sendError(res, error);
    }
  });

  return router;
}
