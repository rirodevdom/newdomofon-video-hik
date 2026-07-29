import type { Response } from 'express';
import { ZodError } from 'zod';

export function parseDateQuery(value: unknown, name: string): Date {
  const date = new Date(String(value || ''));
  if (!Number.isFinite(date.getTime())) throw Object.assign(new Error(`${name} must be a valid ISO date`), { statusCode: 400 });
  return date;
}

export function statusForError(error: unknown): number {
  if (error instanceof ZodError) return 400;
  if (error && typeof error === 'object' && 'statusCode' in error) {
    const value = Number((error as { statusCode?: unknown }).statusCode);
    if (Number.isInteger(value) && value >= 400 && value <= 599) return value;
  }
  return 500;
}

export function sendError(res: Response, error: unknown): void {
  const status = statusForError(error);
  const message = error instanceof ZodError
    ? error.issues.map((issue) => `${issue.path.join('.') || 'body'}: ${issue.message}`).join('; ')
    : error instanceof Error ? error.message : String(error);
  res.status(status).json({ error: message });
}
