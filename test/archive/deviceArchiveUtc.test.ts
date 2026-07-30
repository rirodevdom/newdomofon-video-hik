import assert from 'node:assert/strict';
import test from 'node:test';

process.env.DVR_NODE_TOKEN ||= 'test-node-token';
process.env.DVR_NODE_MEDIA_SECRET ||= 'test-media-secret';
process.env.HIK_NODE_STATE_KEY ||= '0123456789abcdef0123456789abcdef';

const archiveModule = import('../../src/archive/deviceArchive.js');

const device = {
  id: 'nvr-1',
  name: 'DS-H208QA',
  host: '10.110.56.20',
  scheme: 'http',
  isapi_port: 80,
  rtsp_port: 554,
  username: 'admin',
  password: 'secret',
  archive_storage: 'device',
  retention_days: 7,
  enabled: true,
  reject_unauthorized_tls: false
} as const;

test('legacy archive search uses the RaCM searchResultPostion field', async () => {
  const { buildArchiveSearchRequestXml } = await archiveModule;
  const xml = buildArchiveSearchRequestXml(
    'search-1',
    '101',
    new Date('2026-07-30T18:00:00Z'),
    new Date('2026-07-30T18:05:00Z'),
    64
  );

  assert.match(xml, /<trackList><trackID>101<\/trackID><\/trackList>/);
  assert.match(xml, /<contentTypeList><contentType>video<\/contentType><\/contentTypeList>/);
  assert.match(xml, /<searchResultPostion>64<\/searchResultPostion>/);
  assert.doesNotMatch(xml, /<searchResultPosition>/);
});

test('playback URI replaces recording-file boundaries with selected UTC time', async () => {
  const { normalizePlaybackUri } = await archiveModule;
  const result = normalizePlaybackUri(
    'rtsp://127.0.0.1/Streaming/tracks/101/?starttime=20260730T163900Z&endtime=20260730T173900Z&name=ch01_file&size=12345',
    device,
    '101',
    new Date('2026-07-30T18:12:34Z'),
    new Date('2026-07-30T18:17:34Z')
  );
  const url = new URL(result);

  assert.equal(url.hostname, '10.110.56.20');
  assert.equal(url.port, '554');
  assert.equal(url.username, 'admin');
  assert.equal(url.password, 'secret');
  assert.equal(url.searchParams.get('starttime'), '20260730T181234Z');
  assert.equal(url.searchParams.get('endtime'), '20260730T181734Z');
  assert.equal(url.searchParams.get('name'), 'ch01_file');
  assert.equal(url.searchParams.get('size'), '12345');
});
