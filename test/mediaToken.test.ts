import test from 'node:test';
import assert from 'node:assert/strict';

process.env.HIK_NODE_TOKEN ||= 'test-control-token-0123456789';
process.env.HIK_NODE_MEDIA_SECRET ||= 'test-media-secret-0123456789';
process.env.HIK_NODE_STATE_KEY ||= '0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef';
process.env.HIK_NODE_ROOT ||= '/tmp/newdomofon-video-hik-test';

test('creates and verifies scoped media token', async () => {
  const { createMediaToken, verifyMediaToken } = await import('../src/http/mediaToken.js');
  const token = createMediaToken('nvr-1:1', ['live', 'snapshot'], 60);
  const payload = verifyMediaToken(token, 'nvr-1:1', 'live');
  assert.equal(payload.channel_id, 'nvr-1:1');
  assert.deepEqual(payload.scopes, ['live', 'snapshot']);
  assert.throws(() => verifyMediaToken(token, 'nvr-1:2', 'live'));
  assert.throws(() => verifyMediaToken(token, 'nvr-1:1', 'archive'));
});
