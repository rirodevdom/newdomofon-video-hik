import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import test from 'node:test';

const root = process.cwd();
const read = (relative: string) => fs.readFileSync(path.join(root, relative), 'utf8');

test('native helper supports bounded download-by-time archive chunks', () => {
  const worker = read('native-sdk/hik_sdk_worker.cpp');
  assert.match(worker, /NET_DVR_GetFileByTime_V40/);
  assert.match(worker, /NET_DVR_PLAYGETPOS/);
  assert.match(worker, /HIK_SDK_OUTPUT/);
  assert.match(worker, /mode == "download"/);
});

test('media route materializes on-demand SmartYard TS archive segments', () => {
  const routes = read('src/http/mediaRoutes.ts');
  const client = read('src/nativeSdk/client.ts');
  assert.match(routes, /smartyard-virtual-archive-segment/);
  assert.match(routes, /archive\/virtual-segment\.ts/);
  assert.match(routes, /VIRTUAL_ARCHIVE_SEGMENT_MAX_SECONDS = 6/);
  assert.match(routes, /-mpegts_flags', '\+resend_headers'/);
  assert.match(client, /downloadNativeArchiveRange/);
  assert.match(client, /\['download'\]/);
});

test('virtual archive falls back to exact-time grouped playback', () => {
  const routes = read('src/http/mediaRoutes.ts');
  assert.match(routes, /SMARTYARD_VIRTUAL_ARCHIVE_PLAYBACK_FALLBACK/);
  assert.match(routes, /renderVirtualSegmentViaGroupedPlayback/);
  assert.match(routes, /startGroupedPlayback/);
  assert.match(routes, /stopGroupedPlayback/);
  assert.match(routes, /sdkChannel\(found\.channel\)/);
  assert.match(routes, /Virtual archive segment failed: download=/);
  assert.match(routes, /grouped_playback=/);
});
