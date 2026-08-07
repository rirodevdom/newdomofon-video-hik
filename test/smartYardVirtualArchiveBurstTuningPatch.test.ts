import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';

test('SmartYard burst has production timeout headroom and observes background failures', () => {
  const pkg = JSON.parse(fs.readFileSync('package.json', 'utf8')) as { scripts?: { prebuild?: string } };
  const routes = fs.readFileSync('src/http/mediaRoutes.ts', 'utf8');
  const prebuild = pkg.scripts?.prebuild || '';

  assert.match(prebuild, /patch-smartyard-virtual-archive-burst\.py.*patch-smartyard-virtual-archive-burst-tuning\.py/);
  assert.match(routes, /SMARTYARD_VIRTUAL_ARCHIVE_BURST_TUNING/);
  assert.match(routes, /VIRTUAL_ARCHIVE_BURST_TIMEOUT_MS = 60_000/);
  assert.match(routes, /void done\.catch\(\(\) => undefined\)/);
});
