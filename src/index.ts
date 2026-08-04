import fs from 'node:fs/promises';
import express, { type Request, type Response, type NextFunction } from 'express';
import { config } from './config.js';
import { EncryptedStateStore } from './state/encryptedStore.js';
import { HikvisionNodeService } from './service.js';
import { createControlRouter } from './http/controlRoutes.js';
import { createMediaRouter } from './http/mediaRoutes.js';
import { createEventRouter } from './http/eventRoutes.js';
import { DeviceArchiveSessionManager } from './archive/deviceArchiveSessions.js';
import { startMasterAgent } from './master/nodeClient.js';
import { HikvisionEventCollector } from './events/hikvisionEventCollector.js';
import {
  closeHikvisionEventStore,
  getHikvisionEventStoreHealth,
  initializeHikvisionEventStore
} from './events/eventStore.js';

async function main(): Promise<void> {
  await Promise.all([
    fs.mkdir(config.root, { recursive: true, mode: 0o750 }),
    fs.mkdir(config.archiveRoot, { recursive: true, mode: 0o750 }),
    fs.mkdir(config.liveRoot, { recursive: true, mode: 0o750 }),
    fs.mkdir(config.tempRoot, { recursive: true, mode: 0o750 })
  ]);

  initializeHikvisionEventStore();
  const store = new EncryptedStateStore(config.stateFile, config.stateKey);
  const service = new HikvisionNodeService(store);
  const sessions = new DeviceArchiveSessionManager();
  await service.initialize();
  sessions.startCleanup();
  const eventCollector = new HikvisionEventCollector(service);
  eventCollector.start();
  const masterAgent = startMasterAgent(service);

  const app = express();
  app.disable('x-powered-by');
  app.use((req, res, next) => {
    res.setHeader('Access-Control-Allow-Origin', '*');
    res.setHeader('Access-Control-Allow-Methods', 'GET,POST,OPTIONS');
    res.setHeader('Access-Control-Allow-Headers', 'Authorization,Content-Type,X-Hikvision-Media-Token');
    res.setHeader('Access-Control-Expose-Headers', 'Content-Length,Content-Type');
    if (req.method === 'OPTIONS') return res.sendStatus(204);
    return next();
  });
  app.use(express.json({ limit: '1mb' }));

  app.get('/health', (_req, res) => {
    res.json({
      ok: true,
      service: 'newdomofon-video-hik',
      version: '0.4.0',
      devices: service.listDevices().length,
      channels: service.allChannels().length,
      recorders: service.recorderManager.allStatuses().filter((item) => item.running).length,
      archive_policy: ['node', 'device'],
      media: {
        live_hls: true,
        archive_hls: true,
        archive_ranges: true,
        archive_export: true,
        snapshot: true,
        events: true
      },
      events: getHikvisionEventStoreHealth(),
      isapi: true,
      master_pairing: masterAgent.enabled,
      node_kind: 'hikvision'
    });
  });

  app.use('/api/v1/control', createControlRouter(service));
  app.use('/api/v1/media', createMediaRouter(service, sessions));
  app.use('/api/v1/events', createEventRouter(service));

  app.use((error: unknown, _req: Request, res: Response, _next: NextFunction) => {
    const message = error instanceof Error ? error.message : String(error);
    const rawStatus = error && typeof error === 'object' && 'statusCode' in error
      ? Number((error as { statusCode?: unknown }).statusCode)
      : 500;
    const status = Number.isInteger(rawStatus) && rawStatus >= 400 && rawStatus <= 599 ? rawStatus : 500;
    if (status >= 500) console.error(message);
    else console.warn(message);
    res.status(status).json({ error: message });
  });

  const server = app.listen(config.port, config.host, () => {
    console.log(`NewDomofon Hikvision node listening on ${config.host}:${config.port}`);
  });

  const shutdown = (signal: string) => {
    console.log(`Received ${signal}; shutting down`);
    masterAgent.stop();
    eventCollector.stop();
    sessions.stop();
    service.shutdown();
    closeHikvisionEventStore();
    server.close(() => process.exit(0));
    setTimeout(() => process.exit(1), 10_000).unref();
  };
  process.on('SIGTERM', () => shutdown('SIGTERM'));
  process.on('SIGINT', () => shutdown('SIGINT'));
}

main().catch((error) => {
  console.error(error instanceof Error ? error.stack || error.message : error);
  process.exit(1);
});
