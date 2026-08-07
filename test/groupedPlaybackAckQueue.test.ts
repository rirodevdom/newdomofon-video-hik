import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';

test('grouped playback commands are ACK-serialized and late starts are cleaned up', () => {
  const patch = fs.readFileSync('scripts/patch-grouped-playback-ack-queue.py', 'utf8');
  const pkg = JSON.parse(fs.readFileSync('package.json', 'utf8')) as { scripts?: { prebuild?: string } };

  assert.match(patch, /groupedPlaybackCommandQueues/);
  assert.match(patch, /queueGroupedPlaybackCommand/);
  assert.match(patch, /10_000/);
  assert.match(patch, /STOP_PLAYBACK\\t/);
  assert.match(patch, /GROUPED_PLAYBACK_ACK_TIMEOUT_FAST_FAIL/);
  assert.match(patch, /statusCode: 503/);
  assert.match(pkg.scripts?.prebuild || '', /patch-grouped-playback-ack-queue\.py/);
});
