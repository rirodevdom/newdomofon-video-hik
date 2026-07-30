import assert from 'node:assert/strict';
import test from 'node:test';
import type { HikvisionChannel, HikvisionStreamSettings } from '../../src/types.js';
import {
  selectLiveStreamId,
  shouldCopyArchiveAudio,
  shouldCopyArchiveVideo
} from '../../src/media/performance.js';

function stream(id: string, streamType: HikvisionStreamSettings['stream_type'], video: string, audio = 'AAC'): HikvisionStreamSettings {
  return {
    id,
    stream_type: streamType,
    enabled: true,
    name: null,
    video_input_channel_id: 1,
    video_codec: video,
    width: null,
    height: null,
    frame_rate: 25,
    bitrate_kbps: null,
    bitrate_mode: null,
    gop: null,
    audio_codec: audio,
    raw: {}
  };
}

function channel(streams: HikvisionStreamSettings[], primary = streams[0]!.id): HikvisionChannel {
  return {
    id: 'device:1',
    device_id: 'device',
    physical_channel: 1,
    name: 'Camera 1',
    online: true,
    enabled: true,
    primary_stream_id: primary,
    archive_track_ids: ['101'],
    archive_storage: 'device',
    retention_days: 7,
    streams,
    discovered_at: new Date().toISOString()
  };
}

test('auto live policy prefers an H.264 substream over an H.265 primary stream', () => {
  const item = channel([stream('101', 'main', 'H.265'), stream('102', 'sub', 'H.264')]);
  assert.equal(selectLiveStreamId(item, 'auto'), '102');
  assert.equal(selectLiveStreamId(item, 'primary'), '101');
});

test('auto live policy keeps an H.264 primary stream', () => {
  const item = channel([stream('101', 'main', 'H.264'), stream('102', 'sub', 'H.264')]);
  assert.equal(selectLiveStreamId(item, 'auto'), '101');
});

test('archive copy mode is used only for browser-compatible H.264/AAC media', () => {
  const h264 = channel([stream('101', 'main', 'H.264', 'AAC')]);
  const h265 = channel([stream('101', 'main', 'HEVC', 'G.711')]);
  assert.equal(shouldCopyArchiveVideo(h264), true);
  assert.equal(shouldCopyArchiveAudio(h264), true);
  assert.equal(shouldCopyArchiveVideo(h265), false);
  assert.equal(shouldCopyArchiveAudio(h265), false);
});
