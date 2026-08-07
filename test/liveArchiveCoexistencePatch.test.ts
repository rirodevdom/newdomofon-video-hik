import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import test from 'node:test';

const root = process.cwd();

function read(relative: string): string {
  return fs.readFileSync(path.join(root, relative), 'utf8');
}

test('native archive playback keeps same-channel live active by default', () => {
  const worker = read('native-sdk/hik_sdk_device_worker.cpp');
  assert.match(worker, /HIK_SDK_ARCHIVE_PAUSE_LIVE/);
  assert.match(worker, /pauseLiveForArchive \? matchingLive : nullptr/);
  assert.match(worker, /archive keeps live active/);
});

test('live playlist readiness rejects stale manifests', () => {
  const routes = read('src/http/mediaRoutes.ts');
  const index = read('src/index.ts');
  assert.match(routes, /LIVE_PLAYLIST_STALE_MS = 15_000/);
  assert.match(routes, /Date\.now\(\) - stat\.mtimeMs <= LIVE_PLAYLIST_STALE_MS/);
  assert.match(index, /Date\.now\(\) - stat\.mtimeMs <= 15_000/);
});
