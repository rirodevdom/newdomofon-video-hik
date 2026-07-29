import crypto from 'node:crypto';

function md5(input: string): string {
  return crypto.createHash('md5').update(input).digest('hex');
}

export function parseDigestChallenge(header: string): Record<string, string> {
  const source = header.replace(/^Digest\s+/i, '');
  const result: Record<string, string> = {};
  for (const part of source.match(/(?:[^,"]+|"[^"]*")+/g) || []) {
    const index = part.indexOf('=');
    if (index < 0) continue;
    const key = part.slice(0, index).trim();
    const value = part.slice(index + 1).trim().replace(/^"|"$/g, '');
    result[key] = value;
  }
  return result;
}

export function digestAuthorization(
  challenge: Record<string, string>,
  method: string,
  uri: string,
  username: string,
  password: string
): string {
  const realm = challenge.realm || '';
  const nonce = challenge.nonce || '';
  if (!realm || !nonce) throw new Error('Digest challenge has no realm or nonce');
  const qop = (challenge.qop || '')
    .split(',')
    .map((item) => item.trim())
    .find((item) => item === 'auth') || '';
  const algorithm = (challenge.algorithm || 'MD5').toUpperCase();
  if (!['MD5', 'MD5-SESS'].includes(algorithm)) throw new Error(`Unsupported Digest algorithm ${algorithm}`);
  const cnonce = crypto.randomBytes(8).toString('hex');
  const nc = '00000001';
  const baseHa1 = md5(`${username}:${realm}:${password}`);
  const ha1 = algorithm === 'MD5-SESS' ? md5(`${baseHa1}:${nonce}:${cnonce}`) : baseHa1;
  const ha2 = md5(`${method.toUpperCase()}:${uri}`);
  const response = qop
    ? md5(`${ha1}:${nonce}:${nc}:${cnonce}:${qop}:${ha2}`)
    : md5(`${ha1}:${nonce}:${ha2}`);
  const parts = [
    `username="${username}"`,
    `realm="${realm}"`,
    `nonce="${nonce}"`,
    `uri="${uri}"`,
    `response="${response}"`,
    `algorithm=${algorithm}`
  ];
  if (challenge.opaque) parts.push(`opaque="${challenge.opaque}"`);
  if (qop) parts.push(`qop=${qop}`, `nc=${nc}`, `cnonce="${cnonce}"`);
  return `Digest ${parts.join(', ')}`;
}
