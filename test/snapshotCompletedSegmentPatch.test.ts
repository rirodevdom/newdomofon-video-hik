import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

const sourceUrl = new URL('../src/http/mediaRoutes.ts', import.meta.url);

test('snapshot reads a completed live segment instead of the rolling playlist', async () => {
  const source = await readFile(sourceUrl, 'utf8');
  assert.match(source, /newdomofon-hik-snapshot-complete-live-segment-v1/);
  assert.match(source, /latestCompleteLiveSegment/);
  assert.match(source, /path\.join\(root, 'segments'\)/);
  assert.match(source, /SNAPSHOT_RENDER_TIMEOUT_MS = 8_000/);
  assert.match(source, /'-i', segment/);
  assert.match(source, /res\.setHeader\('Content-Length', String\(jpeg\.length\)\)/);
  assert.doesNotMatch(source, /'-i', playlist,\n\s*'-frames:v', '1'/);
});

test('snapshot keeps node-archive relative paths and maps device basenames to segments directory', async () => {
  const source = await readFile(sourceUrl, 'utf8');
  assert.match(source, /archiveStorage === 'device' && !relative\.includes\('\/'\)/);
  assert.match(source, /\? path\.join\(root, 'segments'\)\n\s*: root/);
  assert.match(source, /No completed live segment is available for snapshot/);
});
