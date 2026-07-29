import { XMLParser } from 'fast-xml-parser';

export const xmlParser = new XMLParser({
  ignoreAttributes: false,
  attributeNamePrefix: '@_',
  removeNSPrefix: true,
  trimValues: true,
  parseTagValue: true,
  parseAttributeValue: true
});

export function asArray<T>(value: T | T[] | null | undefined): T[] {
  if (value == null) return [];
  return Array.isArray(value) ? value : [value];
}

export function objectValue(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

export function scalar(value: unknown): string | null {
  if (value == null) return null;
  if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') return String(value);
  return null;
}

export function numberValue(value: unknown): number | null {
  const raw = scalar(value);
  if (raw == null) return null;
  const parsed = Number(raw);
  return Number.isFinite(parsed) ? parsed : null;
}

export function boolValue(value: unknown): boolean | null {
  const raw = scalar(value)?.toLowerCase();
  if (!raw) return null;
  if (['true', '1', 'yes', 'on', 'online'].includes(raw)) return true;
  if (['false', '0', 'no', 'off', 'offline'].includes(raw)) return false;
  return null;
}

export function findObjects(value: unknown, key: string): Record<string, unknown>[] {
  if (!value || typeof value !== 'object') return [];
  if (Array.isArray(value)) return value.flatMap((item) => findObjects(item, key));
  const object = value as Record<string, unknown>;
  const direct = asArray(object[key]).map(objectValue).filter((item) => Object.keys(item).length > 0);
  return [...direct, ...Object.values(object).flatMap((item) => findObjects(item, key))];
}

export function firstScalar(value: unknown, keys: string[]): string | null {
  if (!value || typeof value !== 'object') return null;
  if (Array.isArray(value)) {
    for (const item of value) {
      const found = firstScalar(item, keys);
      if (found != null) return found;
    }
    return null;
  }
  const object = value as Record<string, unknown>;
  for (const key of keys) {
    const found = scalar(object[key]);
    if (found != null) return found;
  }
  for (const child of Object.values(object)) {
    const found = firstScalar(child, keys);
    if (found != null) return found;
  }
  return null;
}
