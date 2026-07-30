import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import test from 'node:test';

test('media readiness patch is materialized before tests', async () => {
  const [routes, index, updater] = await Promise.all([
    fs.readFile(new URL('../../src/http/mediaRoutes.ts', import.meta.url), 'utf8'),
    fs.readFile(new URL('../../src/index.ts', import.meta.url), 'utf8'),
    fs.readFile(new URL('../../scripts/update-installed-project.sh', import.meta.url), 'utf8')
  ]);
  assert.match(routes, /CHANNEL_READY_TIMEOUT_MS = 20_000/);
  assert.match(routes, /waitForLivePlaylist/);
  assert.match(index, /live_expected/);
  assert.match(index, /live_ready/);
  assert.match(updater, /live_ready < live_expected/);
});
