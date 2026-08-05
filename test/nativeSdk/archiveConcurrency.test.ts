import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';

const configSource = fs.readFileSync('src/config.ts', 'utf8');
const clientSource = fs.readFileSync('src/nativeSdk/client.ts', 'utf8');
const runtimeSource = fs.readFileSync('src/nativeSdk/deviceRuntime.ts', 'utf8');
const sessionsSource = fs.readFileSync('src/nativeSdk/archiveSessions.ts', 'utf8');
const workerSource = fs.readFileSync('native-sdk/hik_sdk_device_worker.cpp', 'utf8');

test('native archive pool defaults to four independent playback producers per DVR', () => {
  assert.match(configSource, /HIK_DEVICE_ARCHIVE_MAX_ACTIVE_PER_DVR', 4/);
  assert.match(clientSource, /HIK_SDK_MAX_PLAYBACKS: String\(config\.deviceArchiveMaxActivePerDvr\)/);
  assert.match(workerSource, /HIK_SDK_MAX_PLAYBACKS/);
});

test('new archive windows no longer retire every previous session on the same camera', () => {
  assert.doesNotMatch(sessionsSource, /retireChannel\(channel\.id, id\)/);
  assert.match(sessionsSource, /ensureDeviceCapacity\(device\.id, id\)/);
  assert.match(sessionsSource, /ARCHIVE_SLOT_IDLE_MS = 30_000/);
  assert.match(sessionsSource, /statusCode: 429/);
});

test('same-channel live resumes only after the last archive playback stops', () => {
  assert.match(workerSource, /has_playback_for_channel/);
  assert.match(workerSource, /resumeLive && !has_playback_for_channel\(playbacks, playbackSdkChannel\)/);
  assert.match(workerSource, /!has_playback_for_channel\(playbacks, sdkChannel\)/);
});

test('worker capacity failures are surfaced as HTTP 429 rather than generic 502', () => {
  assert.match(runtimeSource, /stage === 'capacity' \? 429 : 502/);
});
