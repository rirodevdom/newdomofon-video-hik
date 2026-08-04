import { config } from '../config.js';
import { RecorderManager } from '../media/recorderManager.js';
import type { HikvisionChannel, HikvisionDeviceConfig, RecorderStatus } from '../types.js';
import { nativeSdkAvailable } from './client.js';
import { NativeSdkRecorderManager } from './recorderManager.js';

export interface RecorderManagerLike {
  reconcile(devices: Array<{ config: HikvisionDeviceConfig; channels: HikvisionChannel[] }>): Promise<void>;
  stopAll(): void;
  status(channelId: string): RecorderStatus;
  allStatuses(): RecorderStatus[];
}

export function nativeSdkActive(): boolean {
  return config.nativeSdkPreferred && nativeSdkAvailable();
}

export function createRecorderManager(): RecorderManagerLike {
  if (nativeSdkActive()) {
    console.log(`[hikvision-transport] native HCNetSDK enabled: ${config.nativeSdkWorker}`);
    return new NativeSdkRecorderManager();
  }
  if (config.nativeSdkPreferred && !config.nativeSdkFallback) {
    throw new Error(`Native HCNetSDK is required but worker is unavailable: ${config.nativeSdkWorker}`);
  }
  console.warn('[hikvision-transport] HCNetSDK unavailable; using legacy compatibility transport');
  return new RecorderManager();
}
