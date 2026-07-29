import test from 'node:test';
import assert from 'node:assert/strict';

process.env.HIK_NODE_TOKEN ||= 'test-control-token-0123456789';
process.env.HIK_NODE_MEDIA_SECRET ||= 'test-media-secret-0123456789';
process.env.HIK_NODE_STATE_KEY ||= '0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef';
process.env.HIK_NODE_ROOT ||= '/tmp/newdomofon-video-hik-runtime-status-test';

test('keeps explicit ISAPI status', async () => {
  const { resolveChannelOnlineStatus } = await import('../src/service.js');
  assert.equal(resolveChannelOnlineStatus(true, { running: false, restarts: 2, last_error: 'failed' }), true);
  assert.equal(resolveChannelOnlineStatus(false, { running: true, restarts: 0, last_error: null }), false);
});

test('uses running recorder when ISAPI status is unknown', async () => {
  const { resolveChannelOnlineStatus } = await import('../src/service.js');
  assert.equal(resolveChannelOnlineStatus(null, { running: true, restarts: 0, last_error: null }), true);
});

test('marks failed recorder offline when ISAPI status is unknown', async () => {
  const { resolveChannelOnlineStatus } = await import('../src/service.js');
  assert.equal(resolveChannelOnlineStatus(null, { running: false, restarts: 1, last_error: 'RTSP failed' }), false);
  assert.equal(resolveChannelOnlineStatus(null, { running: false, restarts: 0, last_error: null }), null);
});
