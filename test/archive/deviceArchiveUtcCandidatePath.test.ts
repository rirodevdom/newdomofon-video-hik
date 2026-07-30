import assert from 'node:assert/strict';
import test from 'node:test';

process.env.DVR_NODE_TOKEN ||= 'test-node-token';
process.env.DVR_NODE_MEDIA_SECRET ||= 'test-media-secret';
process.env.HIK_NODE_STATE_KEY ||= '0123456789abcdef0123456789abcdef';

const archiveModule = import('../../src/archive/deviceArchive.js');

const device = {
  id: 'nvr-1', name: 'DS-H208QA', host: '10.110.56.20', scheme: 'http',
  isapi_port: 80, rtsp_port: 554, username: 'admin', password: 'secret',
  archive_storage: 'device', retention_days: 7, enabled: true,
  reject_unauthorized_tls: false
} as const;

const channel = {
  id: 'nvr-1:1', device_id: 'nvr-1', physical_channel: 1, name: 'Channel 1',
  online: true, enabled: true, primary_stream_id: '101', archive_track_ids: ['101'],
  archive_storage: 'device', retention_days: 7, streams: [],
  discovered_at: '2026-07-30T18:00:00.000Z'
} as const;

test('first archive candidate is the generic track URL without file identity', async () => {
  const { orderDevicePlaybackCandidates } = await archiveModule;
  const start = new Date('2026-07-30T18:12:34Z');
  const end = new Date('2026-07-30T18:17:34Z');
  const candidates = orderDevicePlaybackCandidates(device, channel, [], start, end);
  const first = new URL(candidates[0]!);

  assert.equal(first.pathname, '/Streaming/tracks/101');
  assert.equal(first.searchParams.get('starttime'), '20260730T181234Z');
  assert.equal(first.searchParams.get('endtime'), '20260730T181734Z');
  assert.equal(first.searchParams.has('name'), false);
  assert.equal(first.searchParams.has('size'), false);
});
