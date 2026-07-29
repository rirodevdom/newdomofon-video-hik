import path from 'node:path';
import { config } from '../config.js';

export function safeId(value: string): string {
  const normalized = value.replace(/[^A-Za-z0-9._-]+/g, '_').replace(/^_+|_+$/g, '');
  if (!normalized) throw new Error('Unsafe empty identifier');
  return normalized.slice(0, 180);
}

export function channelDirName(channelId: string): string {
  return safeId(channelId);
}

export function liveDir(channelId: string): string {
  return path.join(config.liveRoot, channelDirName(channelId));
}

export function archiveDir(channelId: string): string {
  return path.join(config.archiveRoot, channelDirName(channelId));
}
