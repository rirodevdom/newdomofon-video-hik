import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import test from 'node:test';

const root = path.resolve(import.meta.dirname, '..');

function read(relative: string): string {
  return fs.readFileSync(path.join(root, relative), 'utf8');
}

test('SmartYard archive playback is isolated from live grouped worker', () => {
  const runtime = read('src/nativeSdk/deviceRuntime.ts');
  const recorder = read('src/nativeSdk/recorderManager.ts');
  const client = read('src/nativeSdk/client.ts');
  const worker = read('native-sdk/hik_sdk_device_worker.cpp');

  assert.match(runtime, /NATIVE_ARCHIVE_WORKER_ISOLATION/);
  assert.match(runtime, /NATIVE_ARCHIVE_WORKER_POOL/);
  assert.match(runtime, /archiveRuntimes/);
  assert.match(runtime, /archiveRuntimeFactories/);
  assert.match(runtime, /groupedPlaybackAssignments/);
  assert.match(runtime, /\[hcnetsdk-archive:\$\{deviceId\}:\$\{workerIndex\}\] grouped archive runtime started/);
  assert.match(runtime, /Grouped HCNetSDK archive worker \$\{workerIndex\} is not ready/);
  assert.match(recorder, /HIK_SDK_DEVICE_ARCHIVE_ONLY: '1'/);
  assert.match(recorder, /HIK_SDK_ARCHIVE_WORKER_INDEX: String\(workerIndex\)/);
  assert.match(recorder, /HIK_SDK_MAX_PLAYBACKS: String\(config\.deviceArchiveMaxActivePerWorker\)/);
  assert.match(client, /HIK_SDK_DEVICE_LIVE_CONFIG: liveConfigPath/);
  assert.match(worker, /NATIVE_ARCHIVE_WORKER_ISOLATION/);
  assert.match(worker, /HIK_SDK_DEVICE_ARCHIVE_ONLY/);
});

test('SmartYard archive burst ceiling follows the 48-viewer scale configuration', () => {
  const media = read('src/http/mediaRoutes.ts');

  assert.match(media, /SMARTYARD_VIRTUAL_ARCHIVE_MULTIVIEWER/);
  assert.match(media, /VIRTUAL_ARCHIVE_MAX_BURSTS_PER_DEVICE = config\.smartyardArchiveMaxBurstsPerDvr/);
  assert.match(media, /Map<string, VirtualArchiveBurst\[\]>/);
  assert.match(media, /activeVirtualArchiveBurstCount/);
  assert.match(media, /findVirtualArchiveBurst/);
  assert.match(media, /statusCode: 429/);

  const start = media.indexOf('function startVirtualArchiveBurst(');
  const end = media.indexOf('async function waitForBurstSegment(', start);
  assert.ok(start >= 0 && end > start, 'startVirtualArchiveBurst must be materialized');
  const block = media.slice(start, end);
  assert.doesNotMatch(block, /existing\.controller\.abort\(\)/);
  assert.match(block, /bursts\.push\(burst\)/);
});

test('archive worker pool materializes after isolation and multiviewer patches', () => {
  const pkg = JSON.parse(read('package.json')) as { scripts: { prebuild: string } };
  const prebuild = pkg.scripts.prebuild;
  const burst = prebuild.indexOf('patch-smartyard-virtual-archive-burst-tuning.py');
  const isolate = prebuild.indexOf('patch-native-archive-worker-isolation.py');
  const multiviewer = prebuild.indexOf('patch-smartyard-virtual-archive-multiviewer.py');
  const pool = prebuild.indexOf('patch-native-archive-worker-pool.py');

  assert.ok(burst >= 0 && isolate > burst && multiviewer > isolate && pool > multiviewer);
});
