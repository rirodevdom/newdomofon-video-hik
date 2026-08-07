import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';

const configSource = fs.readFileSync('src/config.ts', 'utf8');
const clientSource = fs.readFileSync('src/nativeSdk/client.ts', 'utf8');
const runtimeSource = fs.readFileSync('src/nativeSdk/deviceRuntime.ts', 'utf8');
const sessionsSource = fs.readFileSync('src/nativeSdk/archiveSessions.ts', 'utf8');
const mediaRoutesSource = fs.readFileSync('src/http/mediaRoutes.ts', 'utf8');
const workerSource = fs.readFileSync('native-sdk/hik_sdk_device_worker.cpp', 'utf8');

test('native archive pool defaults to 48 sessions sharded across three workers', () => {
  assert.match(configSource, /HIK_DEVICE_ARCHIVE_MAX_ACTIVE_PER_DVR', 48/);
  assert.match(configSource, /HIK_DEVICE_ARCHIVE_WORKER_COUNT', 3/);
  assert.match(configSource, /HIK_DEVICE_ARCHIVE_MAX_ACTIVE_PER_WORKER', 16/);
  assert.match(configSource, /HIK_SMARTYARD_ARCHIVE_MAX_BURSTS_PER_DVR', 48/);
  assert.match(clientSource, /HIK_SDK_MAX_PLAYBACKS: String\(config\.deviceArchiveMaxActivePerDvr\)/);
  assert.match(workerSource, /HIK_SDK_MAX_PLAYBACKS/);
  assert.match(runtimeSource, /NATIVE_ARCHIVE_WORKER_POOL/);
  assert.match(runtimeSource, /groupedPlaybackAssignments/);
  assert.match(runtimeSource, /workerQueueKey/);
  assert.match(runtimeSource, /config\.deviceArchiveMaxActivePerWorker/);
});

test('new archive windows no longer retire every previous session on the same camera', () => {
  assert.doesNotMatch(sessionsSource, /retireChannel\(channel\.id, id\)/);
  assert.match(sessionsSource, /ensureDeviceCapacity\(device\.id, id\)/);
  assert.match(sessionsSource, /ARCHIVE_SLOT_IDLE_MS = 30_000/);
  assert.match(sessionsSource, /statusCode: 429/);
});

test('near-identical requests on the same camera share a producer instead of consuming pool slots', () => {
  assert.match(sessionsSource, /ARCHIVE_REQUEST_COALESCE_MS = 5_000/);
  assert.match(sessionsSource, /candidate\.channelId === channel\.id/);
  assert.match(sessionsSource, /Math\.abs\(candidate\.start\.getTime\(\) - start\.getTime\(\)\) <= ARCHIVE_REQUEST_COALESCE_MS/);
  assert.match(sessionsSource, /Math\.abs\(candidate\.end\.getTime\(\) - end\.getTime\(\)\) <= ARCHIVE_REQUEST_COALESCE_MS/);
});

test('one viewer can seek without consuming an extra DVR playback slot', () => {
  assert.match(sessionsSource, /viewerIds: Set<string>/);
  assert.match(sessionsSource, /releaseViewerFromOtherSessions\(device\.id, normalizedViewerId, id\)/);
  assert.match(sessionsSource, /session\.viewerIds\.delete\(viewerId\)/);
  assert.match(sessionsSource, /session\.viewerIds\.size === 0/);
  assert.match(mediaRoutesSource, /viewer_id/);
  assert.match(mediaRoutesSource, /getOrCreate\(found\.device\.config, found\.channel, start, end, viewerId \|\| undefined\)/);
});

test('viewer lease can be explicitly released when a player is destroyed', () => {
  assert.match(sessionsSource, /async releaseViewer\(deviceId: string, viewerId: string\)/);
  assert.match(mediaRoutesSource, /archive\/viewer\/release/);
  assert.match(mediaRoutesSource, /sessions\.releaseViewer\(found\.device\.config\.id, viewerId\)/);
});

test('same-channel live resumes only after the last archive playback stops', () => {
  assert.match(workerSource, /has_playback_for_channel/);
  assert.match(workerSource, /resumeLive && !has_playback_for_channel\(playbacks, playbackSdkChannel\)/);
  assert.match(workerSource, /!has_playback_for_channel\(playbacks, sdkChannel\)/);
});

test('worker capacity failures are surfaced as HTTP 429 rather than generic 502', () => {
  assert.match(runtimeSource, /stage === 'capacity' \? 429 : 502/);
});
