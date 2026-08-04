import { spawn, type ChildProcessWithoutNullStreams } from 'node:child_process';
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

export async function runNativeJson<T>(device: HikvisionDeviceConfig, mode: 'probe' | 'ranges', extra: Record<string, string> = {}): Promise<T> {
  if (!nativeSdkAvailable()) throw new Error(`HCNetSDK worker is not installed: ${config.nativeSdkWorker}`);
  const child = spawn(config.nativeSdkWorker, [mode], {
    env: { ...deviceEnv(device), ...extra },
    stdio: ['ignore', 'pipe', 'pipe']
  });
  let stdout = '';
  let stderr = '';
  child.stdout.on('data', (chunk) => { stdout = `${stdout}${String(chunk)}`.slice(-16 * 1024 * 1024); });
  child.stderr.on('data', (chunk) => { stderr = `${stderr}${String(chunk)}`.slice(-2 * 1024 * 1024); });
  const code = await new Promise<number | null>((resolve, reject) => {
    const timer = setTimeout(() => {
      child.kill('SIGTERM');
      reject(new Error(`HCNetSDK ${mode} exceeded ${config.nativeSdkCommandTimeoutMs} ms`));
    }, config.nativeSdkCommandTimeoutMs);
    timer.unref?.();
    child.once('error', (error) => { clearTimeout(timer); reject(error); });
    child.once('exit', (exitCode) => { clearTimeout(timer); resolve(exitCode); });
  });
  if (code !== 0) throw new Error(`HCNetSDK ${mode} failed (${code}): ${stderr.trim().slice(-2000)}`);
  const lines = stdout.split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
  const jsonLine = [...lines].reverse().find((line) => line.startsWith('{'));
  if (!jsonLine) throw new Error(`HCNetSDK ${mode} returned no JSON payload`);
  return JSON.parse(jsonLine) as T;
}

export function probeNativeDevice(device: HikvisionDeviceConfig): Promise<NativeProbe> {
  return runNativeJson<NativeProbe>(device, 'probe');
}

export async function findNativeArchive(device: HikvisionDeviceConfig, channel: HikvisionChannel, start: Date, end: Date): Promise<NativeArchiveItem[]> {
  const payload = await runNativeJson<{ items?: NativeArchiveItem[] }>(device, 'ranges', {
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
    if (!env.HIK_SDK_CHANNEL) env.HIK_SDK_CHANNEL = String(sdkChannel(channel));
    if (!env.HIK_SDK_STREAM_TYPE) env.HIK_SDK_STREAM_TYPE = '0';
  }
  return spawn(config.nativeSdkWorker, [mode], {
    env,
    stdio: ['pipe', 'pipe', 'pipe']
  });
}
