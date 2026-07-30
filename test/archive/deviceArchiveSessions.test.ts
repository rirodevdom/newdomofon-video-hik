import assert from 'node:assert/strict';
import test from 'node:test';

process.env.DVR_NODE_TOKEN ||= 'test-node-token';
process.env.DVR_NODE_MEDIA_SECRET ||= 'test-media-secret';
process.env.HIK_NODE_STATE_KEY ||= '0123456789abcdef0123456789abcdef';

const sessionsModule = import('../../src/archive/deviceArchiveSessions.js');

test('a newer seek retires only older active sessions for the same channel', async () => {
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
    id: 'already-retired',
    channelId: 'nvr-1:1',
    status: 'retired'
  }, 'nvr-1:1', keepId), false);

  assert.equal(shouldRetireArchiveSession({
    id: 'already-cancelled',
    channelId: 'nvr-1:1',
    status: 'cancelled'
  }, 'nvr-1:1', keepId), false);
});

test('a retired session is deleted only after its HLS grace period', async () => {
  const { shouldDeleteRetiredArchiveSession } = await sessionsModule;
  const retiredAt = 1_000;

  assert.equal(shouldDeleteRetiredArchiveSession({
    status: 'retired',
    retiredAt
  }, retiredAt + 44_999, 45_000), false);

  assert.equal(shouldDeleteRetiredArchiveSession({
    status: 'retired',
    retiredAt
  }, retiredAt + 45_000, 45_000), true);

  assert.equal(shouldDeleteRetiredArchiveSession({
    status: 'ready',
    retiredAt: null
  }, retiredAt + 60_000, 45_000), false);
});
