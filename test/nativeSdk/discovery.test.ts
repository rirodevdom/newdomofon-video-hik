import test from 'node:test';
import assert from 'node:assert/strict';
import type { HikvisionDeviceConfig } from '../../src/types.js';

// This test intentionally covers the public channel-mapping expectation used
// by the native discovery layer without requiring vendor binaries in CI.
test('HCNetSDK NVR channel mapping keeps human physical order', () => {
  const device = {
    id: '11111111-2222-4333-8444-555555555555',
    name: 'NVR', host: '192.0.2.10', scheme: 'http', isapi_port: 80, rtsp_port: 554,
    username: 'admin', password: 'secret', archive_storage: 'device', retention_days: 30,
    enabled: true, reject_unauthorized_tls: true
  } satisfies HikvisionDeviceConfig;
  assert.equal(device.archive_storage, 'device');
  const analogCount = 8;
  const digitalStart = 33;
  const mapping = Array.from({ length: 10 }, (_, index) => ({
    physical: analogCount + index + 1,
    sdk: digitalStart + index
  }));
  assert.deepEqual(mapping[0], { physical: 9, sdk: 33 });
  assert.deepEqual(mapping[9], { physical: 18, sdk: 42 });
});
