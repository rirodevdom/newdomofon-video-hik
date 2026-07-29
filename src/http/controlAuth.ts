import crypto from 'node:crypto';
import type { Request, Response, NextFunction } from 'express';
import { config } from '../config.js';

function safeEqual(left: string, right: string): boolean {
  const a = Buffer.from(left);
  const b = Buffer.from(right);
  return a.length === b.length && crypto.timingSafeEqual(a, b);
}

export function controlTokenFromRequest(req: Request): string {
  const authorization = String(req.get('authorization') || '');
  if (authorization.startsWith('Bearer ')) return authorization.slice(7).trim();
  return String(req.get('x-hikvision-node-token') || req.query.control_token || '').trim();
}

export function requireControlAuth(req: Request, res: Response, next: NextFunction): void {
  const token = controlTokenFromRequest(req);
  if (!token || !safeEqual(token, config.agentToken)) {
    res.status(401).json({ error: 'Invalid Hikvision node control token' });
    return;
  }
  next();
}
