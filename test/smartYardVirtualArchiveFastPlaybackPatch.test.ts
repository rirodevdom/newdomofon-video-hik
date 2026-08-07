import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import test from 'node:test';

const root = process.cwd();
const read = (relative: string) => fs.readFileSync(path.join(root, relative), 'utf8');

test('SmartYard virtual archive prefers 4x grouped playback', () => {
  const routes = read('src/http/mediaRoutes.ts');
  const runtime = read('src/nativeSdk/deviceRuntime.ts');
  const worker = read('native-sdk/hik_sdk_device_worker.cpp');

  assert.match(routes, /SMARTYARD_VIRTUAL_ARCHIVE_FAST_PLAYBACK/);
  assert.match(routes, /fastSteps: 2/);
  assert.ok(routes.indexOf('renderVirtualSegmentViaGroupedPlayback(found, start, roundedDuration, root, key, temp)') < routes.indexOf('downloadNativeArchiveRange(found.device.config'));
  assert.match(runtime, /fastSteps\?: number/);
  assert.match(worker, /NET_DVR_PLAYFAST/);
  assert.match(worker, /fast_steps=/);
});
