import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';

test('live HLS keeps a recovery buffer beyond the advertised playlist', () => {
  const patch = fs.readFileSync('scripts/patch-live-segment-retention.py', 'utf8');
  const pkg = JSON.parse(fs.readFileSync('package.json', 'utf8')) as { scripts?: { prebuild?: string } };
  const updater = fs.readFileSync('scripts/update-installed-project.sh', 'utf8');

  assert.match(patch, /HIK_LIVE_DELETE_THRESHOLD/);
  assert.match(patch, /numberEnv\('HIK_LIVE_DELETE_THRESHOLD', 60, 2\)/);
  assert.match(patch, /String\(config\.liveDeleteThreshold\)/);
  assert.match(pkg.scripts?.prebuild || '', /patch-live-segment-retention\.py/);
  assert.match(updater, /set_env_default HIK_LIVE_DELETE_THRESHOLD 60/);
});
