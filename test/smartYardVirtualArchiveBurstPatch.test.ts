import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';

test('SmartYard virtual archive reuses one grouped playback for a progressive segment burst', () => {
  const patch = fs.readFileSync('scripts/patch-smartyard-virtual-archive-burst.py', 'utf8');
  const pkg = JSON.parse(fs.readFileSync('package.json', 'utf8')) as { scripts?: { prebuild?: string } };
  const routes = fs.readFileSync('src/http/mediaRoutes.ts', 'utf8');

  assert.match(patch, /SMARTYARD_VIRTUAL_ARCHIVE_BURST/);
  assert.match(patch, /VIRTUAL_ARCHIVE_BURST_SEGMENTS = 15/);
  assert.match(patch, /runVirtualArchiveBurst/);
  assert.match(patch, /'-f', 'segment'/);
  assert.match(patch, /'-segment_time'/);
  assert.match(patch, /fastSteps: 2/);

  const prebuild = pkg.scripts?.prebuild || '';
  const watchdog = prebuild.indexOf('patch-grouped-playback-stop-watchdog.py');
  const burst = prebuild.indexOf('patch-smartyard-virtual-archive-burst.py');
  assert.ok(watchdog >= 0 && burst > watchdog, 'burst patch must run after watchdog patch');

  assert.match(routes, /SMARTYARD_VIRTUAL_ARCHIVE_BURST/);
  assert.match(routes, /VIRTUAL_ARCHIVE_BURST_SEGMENTS = 15/);
  assert.match(routes, /virtualArchiveBursts/);
  assert.match(routes, /runVirtualArchiveBurst/);
  assert.match(routes, /ensureVirtualArchiveSegment/);
  assert.doesNotMatch(routes, /renderVirtualSegmentViaGroupedPlayback/);
});
