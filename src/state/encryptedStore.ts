import crypto from 'node:crypto';
import fs from 'node:fs/promises';
import path from 'node:path';
import type { PersistedState } from '../types.js';

interface Envelope {
  version: 1;
  algorithm: 'aes-256-gcm';
  iv: string;
  tag: string;
  ciphertext: string;
}

function decodeKey(raw: string): Buffer {
  const value = raw.trim();
  const key = /^[0-9a-f]{64}$/i.test(value)
    ? Buffer.from(value, 'hex')
    : Buffer.from(value, 'base64');
  if (key.length !== 32) throw new Error('HIK_NODE_STATE_KEY must decode to exactly 32 bytes');
  return key;
}

export class EncryptedStateStore {
  private readonly key: Buffer;
  private state: PersistedState = { version: 1, devices: [] };

  constructor(private readonly file: string, rawKey: string) {
    this.key = decodeKey(rawKey);
  }

  async load(): Promise<PersistedState> {
    try {
      const raw = await fs.readFile(this.file, 'utf8');
      const envelope = JSON.parse(raw) as Envelope;
      if (envelope.version !== 1 || envelope.algorithm !== 'aes-256-gcm') {
        throw new Error('Unsupported state envelope');
      }
      const decipher = crypto.createDecipheriv('aes-256-gcm', this.key, Buffer.from(envelope.iv, 'base64'));
      decipher.setAuthTag(Buffer.from(envelope.tag, 'base64'));
      const plaintext = Buffer.concat([
        decipher.update(Buffer.from(envelope.ciphertext, 'base64')),
        decipher.final()
      ]);
      const parsed = JSON.parse(plaintext.toString('utf8')) as PersistedState;
      if (parsed.version !== 1 || !Array.isArray(parsed.devices)) throw new Error('Invalid state payload');
      this.state = parsed;
      return structuredClone(this.state);
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code === 'ENOENT') {
        await this.save(this.state);
        return structuredClone(this.state);
      }
      throw error;
    }
  }

  snapshot(): PersistedState {
    return structuredClone(this.state);
  }

  async save(next: PersistedState): Promise<void> {
    this.state = structuredClone(next);
    const iv = crypto.randomBytes(12);
    const cipher = crypto.createCipheriv('aes-256-gcm', this.key, iv);
    const plaintext = Buffer.from(JSON.stringify(this.state));
    const ciphertext = Buffer.concat([cipher.update(plaintext), cipher.final()]);
    const envelope: Envelope = {
      version: 1,
      algorithm: 'aes-256-gcm',
      iv: iv.toString('base64'),
      tag: cipher.getAuthTag().toString('base64'),
      ciphertext: ciphertext.toString('base64')
    };

    await fs.mkdir(path.dirname(this.file), { recursive: true, mode: 0o750 });
    const temp = `${this.file}.${process.pid}.${Date.now()}.tmp`;
    await fs.writeFile(temp, `${JSON.stringify(envelope)}\n`, { mode: 0o600 });
    await fs.rename(temp, this.file);
    await fs.chmod(this.file, 0o600);
  }
}
