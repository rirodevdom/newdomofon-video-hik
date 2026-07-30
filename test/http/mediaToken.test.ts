import assert from 'node:assert/strict';
import crypto from 'node:crypto';
import test from 'node:test';

type MediaScope = 'live' | 'archive' | 'snapshot';

const nodeToken = process.env.DVR_NODE_TOKEN || 'test-node-token-0123456789';
const mediaSecret = process.env.DVR_NODE_MEDIA_SECRET || 'test-media-secret-0123456789';
process.env.DVR_NODE_TOKEN = nodeToken;
process.env.DVR_NODE_MEDIA_SECRET = mediaSecret;
process.env.HIK_NODE_STATE_KEY ||= '0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef';

// config.ts validates required environment variables while the module graph is
// loaded, so import the implementation only after installing test defaults.
const { verifyMediaToken } = await import('../../src/http/mediaToken.js');

function sign(secret: string, channelId: string, scopes: MediaScope[]): string {
  const now = Math.floor(Date.now() / 1000);
  const body = Buffer.from(JSON.stringify({ channel_id: channelId, scopes, iat: now, exp: now + 300 })).toString('base64url');
  const signature = crypto.createHmac('sha256', secret).update(body).digest('base64url');
  return `${body}.${signature}`;
}

test('accepts a media token signed with SHA-256(DVR_NODE_TOKEN)', () => {
  const derived = crypto.createHash('sha256').update(nodeToken).digest('hex');
  const payload = verifyMediaToken(sign(derived, 'device:1', ['live']), 'device:1', 'live');
  assert.equal(payload.channel_id, 'device:1');
});

test('still accepts the configured DVR_NODE_MEDIA_SECRET', () => {
  const payload = verifyMediaToken(sign(mediaSecret, 'device:2', ['archive']), 'device:2', 'archive');
  assert.equal(payload.channel_id, 'device:2');
});

test('rejects an unrelated media-token signature', () => {
  assert.throws(
    () => verifyMediaToken(sign('unrelated-secret', 'device:3', ['snapshot']), 'device:3', 'snapshot'),
    /Invalid media token signature/
  );
});
