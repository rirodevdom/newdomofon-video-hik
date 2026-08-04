import { spawn, spawnSync, type ChildProcessWithoutNullStreams } from 'node:child_process';
import fs from 'node:fs';
import type { HikvisionChannel, HikvisionDeviceConfig } from '../types.js';
import { config } from '../config.js';

export interface NativeProbe {
  ok: boolean;
  transport: string;
  serial: string;
  analog_start: number;
  analog_count: number;
  digital_start: number;
  digital_count: number;
  main_proto: number;
  sub_proto: number;
}

export interface NativeArchiveItem {
  start: string;
  end: string;
  file_type: number;
  stream_type: number;
  file_index: number;
}

function deviceEnv(device: HikvisionDeviceConfig): NodeJS.ProcessEnv {
  return {
    ...process.env,
    HIK_SDK_HOST: device.host,
    HIK_SDK_PORT: String(config.nativeSdkDefaultPort),
    HIK_SDK_USERNAME: device.username,
    HIK_SDK_PASSWORD: device.password
  };
}

export function nativeSdkAvailable(): boolean {
  try {
    fs.accessSync(config.nativeSdkWorker, fs.constants.X_OK);
    return true;
  } catch {
    return false;
  }
}

export function sdkChannel(channel: HikvisionChannel): number {
  const direct = Number(channel.sdk_channel ?? channel.physical_channel);
  if (!Number.isInteger(direct) || direct <= 0) throw new Error(`Invalid HCNetSDK channel for ${channel.id}`);
  return direct;
}

export function runNativeJson<T>(device: HikvisionDeviceConfig, mode: 'probe' | 'ranges', extra: Record<string, string> = {}): T {
  if (!nativeSdkAvailable()) throw new Error(`HCNetSDK worker is not installed: ${config.nativeSdkWorker}`);
  const result = spawnSync(config.nativeSdkWorker, [mode], {
    env: { ...deviceEnv(device), ...extra },
    encoding: 'utf8',
    timeout: config.nativeSdkCommandTimeoutMs,
    maxBuffer: 16 * 1024 * 1024
  });
  if (result.error) throw result.error;
  if (result.status !== 0) {
    throw new Error(`HCNetSDK ${mode} failed (${result.status}): ${(result.stderr || '').trim().slice(-2000)}`);
  }
  const lines = String(result.stdout || '').split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
  const jsonLine = [...lines].reverse().find((line) => line.startsWith('{'));
  if (!jsonLine) throw new Error(`HCNetSDK ${mode} returned no JSON payload`);
  return JSON.parse(jsonLine) as T;
}

export function probeNativeDevice(device: HikvisionDeviceConfig): NativeProbe {
  return runNativeJson<NativeProbe>(device, 'probe');
}

export function findNativeArchive(device: HikvisionDeviceConfig, channel: HikvisionChannel, start: Date, end: Date): NativeArchiveItem[] {
  const payload = runNativeJson<{ items?: NativeArchiveItem[] }>(device, 'ranges', {
    HIK_SDK_CHANNEL: String(sdkChannel(channel)),
    HIK_SDK_START: start.toISOString(),
    HIK_SDK_END: end.toISOString(),
    HIK_SDK_STREAM_TYPE: '0'
  });
  return Array.isArray(payload.items) ? payload.items : [];
}

export function spawnNativeStream(
  device: HikvisionDeviceConfig,
  channel: HikvisionChannel,
  mode: 'live' | 'playback' | 'events',
  extra: Record<string, string> = {}
): ChildProcessWithoutNullStreams {
  if (!nativeSdkAvailable()) throw new Error(`HCNetSDK worker is not installed: ${config.nativeSdkWorker}`);
  const env: NodeJS.ProcessEnv = {
    ...deviceEnv(device),
    ...extra
  };
  if (mode !== 'events') {
    env.HIK_SDK_CHANNEL = String(sdkChannel(channel));
    env.HIK_SDK_STREAM_TYPE = '0';
  }
  return spawn(config.nativeSdkWorker, [mode], {
    env,
    stdio: ['pipe', 'pipe', 'pipe']
  });
}
