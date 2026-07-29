import crypto from 'node:crypto';
import type { Request } from 'express';
import { config } from '../config.js';
import { controlTokenFromRequest } from './controlAuth.js';

export type MediaScope = 'live' | 'archive' | 'snapshot';

export interface MediaTokenPayload {
  channel_id: string;
  scopes: MediaScope[];
  exp: number;
  iat: number;
}

function signBodyWith(secret: string, body: string): string {
  return crypto.createHmac('sha256', secret).update(body).digest('base64url');
}

function primaryMediaSignature(body: string): string {
  return signBodyWith(config.mediaSecret, body);
}

function agentDerivedMediaSecret(): string {
  // Master stores only SHA-256(DVR_NODE_TOKEN), while the node owns the raw
  // token. Deriving the HMAC key this way gives both sides the same media-only
  // credential without exposing or persisting the raw agent token on master.
  return crypto.createHash('sha256').update(config.nodeToken).digest('hex');
}

function unauthorized(message: string): Error & { statusCode: number } {
  return Object.assign(new Error(message), { statusCode: 401 });
}

function safeEqual(left: string, right: string): boolean {
  const a = Buffer.from(left);
  const b = Buffer.from(right);
  return a.length === b.length && crypto.timingSafeEqual(a, b);
}

function signatureAccepted(body: string, signature: string): boolean {
  if (safeEqual(signature, primaryMediaSignature(body))) return true;
  return safeEqual(signature, signBodyWith(agentDerivedMediaSecret(), body));
}

export function createMediaToken(channelId: string, scopes: MediaScope[], ttlSeconds: number): string {
  const now = Math.floor(Date.now() / 1000);
  const ttl = Math.max(1, Math.min(Math.floor(ttlSeconds), config.mediaTokenMaxSeconds));
  const payload: MediaTokenPayload = { channel_id: channelId, scopes, iat: now, exp: now + ttl };
  const body = Buffer.from(JSON.stringify(payload)).toString('base64url');
  return `${body}.${primaryMediaSignature(body)}`;
}

export function verifyMediaToken(raw: string, channelId: string, scope: MediaScope): MediaTokenPayload {
  const [body, signature, extra] = raw.split('.');
  if (!body || !signature || extra || !signatureAccepted(body, signature)) {
    throw unauthorized('Invalid media token signature');
  }

  let payload: MediaTokenPayload;
  try {
    payload = JSON.parse(Buffer.from(body, 'base64url').toString('utf8')) as MediaTokenPayload;
  } catch {
    throw unauthorized('Invalid media token payload');
  }

  if (payload.channel_id !== channelId) throw unauthorized('Media token channel mismatch');
  if (!Array.isArray(payload.scopes) || !payload.scopes.includes(scope)) throw unauthorized('Media token scope mismatch');
  if (!Number.isFinite(payload.exp) || payload.exp < Math.floor(Date.now() / 1000)) throw unauthorized('Media token expired');
  return payload;
}

export function mediaTokenFromRequest(req: Request): string {
  const authorization = String(req.get('authorization') || '');
  if (authorization.startsWith('Bearer ')) return authorization.slice(7).trim();
  return String(req.query.token || req.get('x-hikvision-media-token') || '').trim();
}

export function isControlAuthorized(req: Request): boolean {
  const token = controlTokenFromRequest(req);
  if (!token) return false;
  const left = Buffer.from(token);
  const right = Buffer.from(config.agentToken);
  return left.length === right.length && crypto.timingSafeEqual(left, right);
}

export function playlistMediaToken(req: Request, channelId: string, scope: MediaScope): string {
  if (isControlAuthorized(req)) return createMediaToken(channelId, [scope], Math.min(120, config.mediaTokenMaxSeconds));
  const token = mediaTokenFromRequest(req);
  if (!token) throw unauthorized('Missing media token');
  verifyMediaToken(token, channelId, scope);
  return token;
}

export function authorizeMedia(req: Request, channelId: string, scope: MediaScope): string {
  if (isControlAuthorized(req)) return '';
  const token = mediaTokenFromRequest(req);
  if (!token) throw unauthorized('Missing media token');
  verifyMediaToken(token, channelId, scope);
  return token;
}
