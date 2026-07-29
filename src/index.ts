import fs from 'node:fs/promises';
import express, { type Request, type Response, type NextFunction } from 'express';
import { config } from './config.js';
import { EncryptedStateStore } from './state/encryptedStore.js';
import { HikvisionNodeService } from './service.js';
import { createControlRouter } from './http/controlRoutes.js';
import { createMediaRouter } from './http/mediaRoutes.js';
import { DeviceArchiveSessionManager } from './archive/deviceArchiveSessions.js';

async function main(): Promise<void> {
  await Promise.all([
    fs.mkdir(config.root, { recursive: true, mode: 0o750 }),
    fs.mkdir(config.archiveRoot, { recursive: true, mode: 0o750 }),
    fs.mkdir(config.liveRoot, { recursive: true, mode: 0o750 }),
    fs.mkdir(config.tempRoot, { recursive: true, mode: 0o750 })
  ]);

  const store = new EncryptedStateStore(config.stateFile, config.stateKey);
  const service = new HikvisionNodeService(store);
  const sessions = new DeviceArchiveSessionManager();
  await service.initialize();
  sessions.startCleanup();

  const app = express();
  app.disable('x-powered-by');
  app.use(express.json({ limit: '1mb' }));

  app.get('/health', (_req, res) => {
    res.json({
      ok: true,
      service: 'newdomofon-video-hik',
      version: '0.1.0',
      devices: service.listDevices().length,
      channels: service.allChannels().length,
      recorders: service.recorderManager.allStatuses().filter((item) => item.running).length,
      archive_policy: ['node', 'device'],
      isapi: true
    });
  });

  app.use('/api/v1/control', createControlRouter(service));
  app.use('/api/v1/media', createMediaRouter(service, sessions));

  app.use((error: unknown, _req: Request, res: Response, _next: NextFunction) => {
    const message = error instanceof Error ? error.message : String(error);
    console.error(message);
    res.status(500).json({ error: message });
  });

  const server = app.listen(config.port, config.host, () => {
    console.log(`NewDomofon Hikvision node listening on ${config.host}:${config.port}`);
  });

  const shutdown = (signal: string) => {
    console.log(`Received ${signal}; shutting down`);
    sessions.stop();
    service.shutdown();
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
