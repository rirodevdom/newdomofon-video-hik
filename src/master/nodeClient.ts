import fs from 'node:fs/promises';
import os from 'node:os';
import { config, isMasterPairingConfigured } from '../config.js';
import type { HikvisionNodeService } from '../service.js';
import type { HikvisionDeviceConfig, HikvisionDeviceSnapshot } from '../types.js';

interface MasterConfigResponse {
  node_id: string;
  node_name: string;
  node_kind?: string;
  media_secret: string;
  config_generation: string;
  devices?: HikvisionDeviceConfig[];
}

interface NodeCommand {
  id: string;
  type: string;
  payload?: Record<string, unknown>;
}

interface CommandsResponse {
  items?: NodeCommand[];
}

export interface MasterAgentHandle {
  enabled: boolean;
  stop(): void;
}

function headers(): Record<string, string> {
  return {
    authorization: `Bearer ${config.nodeToken}`,
    'x-node-id': config.nodeId,
    'x-node-protocol-version': '1',
    accept: 'application/json',
    'content-type': 'application/json'
  };
}

async function masterRequest<T>(path: string, init: RequestInit = {}): Promise<T> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), config.masterRequestTimeoutMs);
  try {
    const requestHeaders = new Headers(init.headers);
    for (const [key, value] of Object.entries(headers())) {
      if (!requestHeaders.has(key)) requestHeaders.set(key, value);
    }
    const response = await fetch(`${config.masterUrl}${path}`, {
      ...init,
      signal: controller.signal,
      headers: requestHeaders
    });
    const text = await response.text();
    if (!response.ok) {
      const error = new Error(`Master ${path} HTTP ${response.status}: ${text.slice(0, 300)}`) as Error & { statusCode?: number };
      error.statusCode = response.status;
      throw error;
    }
    return (text ? JSON.parse(text) : {}) as T;
  } finally {
    clearTimeout(timer);
  }
}

async function storageSnapshot(): Promise<Record<string, unknown>> {
  try {
    const stat = await fs.statfs(config.archiveRoot);
    const blockSize = Number(stat.bsize || 0);
    const totalBytes = Number(stat.blocks || 0) * blockSize;
    const freeBytes = Number(stat.bavail || stat.bfree || 0) * blockSize;
    return {
      root: config.archiveRoot,
      total_bytes: totalBytes,
      free_bytes: freeBytes,
      used_bytes: Math.max(0, totalBytes - freeBytes)
    };
  } catch (error) {
    return {
      root: config.archiveRoot,
      error: error instanceof Error ? error.message : String(error)
    };
  }
}

async function heartbeat(service: HikvisionNodeService): Promise<void> {
  await masterRequest('/api/node-agent/heartbeat', {
    method: 'POST',
    body: JSON.stringify({
      public_base_url: config.publicBaseUrl,
      internal_url: config.internalUrl,
      version: '0.2.0',
      capabilities: {
        node_kind: 'hikvision',
        hostname: os.hostname(),
        hikvision_isapi: true,
        live_hls: true,
        snapshot: true,
        archive_node: true,
        archive_device: true,
        contract_version: 1,
        devices: service.listDevices().length,
        channels: service.allChannels().length
      },
      storage: await storageSnapshot()
    })
  });
}

async function loadConfig(): Promise<MasterConfigResponse> {
  return masterRequest<MasterConfigResponse>('/api/node-agent/config');
}

async function reportDiscovery(devices: HikvisionDeviceSnapshot[]): Promise<void> {
  await masterRequest('/api/node-agent/hikvision/sync', {
    method: 'POST',
    body: JSON.stringify({ devices })
  });
}

async function commandResult(command: NodeCommand, status: 'done' | 'failed', result: Record<string, unknown>): Promise<void> {
  await masterRequest(`/api/node-agent/commands/${encodeURIComponent(command.id)}/result`, {
    method: 'POST',
    body: JSON.stringify({ status, result })
  });
}

async function processCommands(service: HikvisionNodeService): Promise<void> {
  const response = await masterRequest<CommandsResponse>('/api/node-agent/commands?limit=20');
  for (const command of response.items || []) {
    try {
      switch (command.type) {
        case 'reload_cameras':
        case 'reload_devices':
        case 'sync_devices':
          await service.syncAll();
          break;
        case 'restart_recordings':
          await service.restartRecorders();
          break;
        case 'health_check':
          break;
        default:
          throw new Error(`Unsupported Hikvision-node command: ${command.type}`);
      }
      await reportDiscovery(service.listDevices(true));
      await commandResult(command, 'done', { ok: true, command: command.type });
    } catch (error) {
      await commandResult(command, 'failed', {
        error: error instanceof Error ? error.message : String(error),
        command: command.type
      }).catch(() => undefined);
    }
  }
}

export function startMasterAgent(service: HikvisionNodeService): MasterAgentHandle {
  if (!isMasterPairingConfigured()) {
    console.warn('[master-agent] disabled: deploy node first with DVR_MASTER_URL, DVR_NODE_ID, DVR_NODE_TOKEN, DVR_NODE_PUBLIC_BASE_URL and DVR_NODE_INTERNAL_URL');
    return { enabled: false, stop() {} };
  }

  let stopped = false;
  let configBusy = false;
  let heartbeatBusy = false;
  let lastError = '';

  const logFailure = (scope: string, error: unknown) => {
    const message = error instanceof Error ? error.message : String(error);
    const key = `${scope}:${message}`;
    if (key !== lastError) {
      console.warn(`[master-agent] ${scope}: ${message}`);
      lastError = key;
    }
  };

  const runHeartbeat = async () => {
    if (stopped || heartbeatBusy) return;
    heartbeatBusy = true;
    try {
      await heartbeat(service);
      lastError = '';
    } catch (error) {
      logFailure('heartbeat', error);
    } finally {
      heartbeatBusy = false;
    }
  };

  const runConfig = async () => {
    if (stopped || configBusy) return;
    configBusy = true;
    try {
      const remote = await loadConfig();
      if ((remote.node_kind || 'video') !== 'hikvision') {
        throw new Error(`Master node type is ${remote.node_kind || 'video'}, expected hikvision`);
      }
      if (remote.media_secret !== config.mediaSecret) {
        throw new Error('DVR_NODE_MEDIA_SECRET does not match the master node record');
      }
      await service.reconcileMasterDevices(remote.devices || []);
      await reportDiscovery(service.listDevices(true));
      await processCommands(service);
      lastError = '';
    } catch (error) {
      logFailure('config', error);
    } finally {
      configBusy = false;
    }
  };

  void runHeartbeat();
  void runConfig();
  const heartbeatTimer = setInterval(() => { void runHeartbeat(); }, config.heartbeatSeconds * 1000);
  const configTimer = setInterval(() => { void runConfig(); }, config.masterPollSeconds * 1000);
  heartbeatTimer.unref?.();
  configTimer.unref?.();

  console.log(`[master-agent] pairing enabled node_id=${config.nodeId} master=${config.masterUrl}`);
  return {
    enabled: true,
    stop() {
      stopped = true;
      clearInterval(heartbeatTimer);
      clearInterval(configTimer);
    }
  };
}
