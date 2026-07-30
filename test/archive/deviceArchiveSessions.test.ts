import assert from 'node:assert/strict';
import test from 'node:test';

process.env.DVR_NODE_TOKEN ||= 'test-node-token';
process.env.DVR_NODE_MEDIA_SECRET ||= 'test-media-secret';
process.env.HIK_NODE_STATE_KEY ||= '0123456789abcdef0123456789abcdef';

const sessionsModule = import('../../src/archive/deviceArchiveSessions.js');

test('a newer seek retires only older sessions for the same channel', async () => {
  const { shouldRetireArchiveSession } = await sessionsModule;
  const keepId = 'new-session';

  assert.equal(shouldRetireArchiveSession({
    id: 'old-session',
    channelId: 'nvr-1:1',
    status: 'ready'
  }, 'nvr-1:1', keepId), true);

  assert.equal(shouldRetireArchiveSession({
    id: keepId,
    channelId: 'nvr-1:1',
    status: 'preparing'
  }, 'nvr-1:1', keepId), false);

  assert.equal(shouldRetireArchiveSession({
    id: 'other-channel-session',
    channelId: 'nvr-1:2',
    status: 'ready'
  }, 'nvr-1:1', keepId), false);

  assert.equal(shouldRetireArchiveSession({
    id: 'already-cancelled',
    channelId: 'nvr-1:1',
    status: 'cancelled'
  }, 'nvr-1:1', keepId), false);
});
