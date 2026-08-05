import test from 'node:test';
import assert from 'node:assert/strict';

function groupedReady(devices: Array<{ id: string; expected: boolean; readyChannels: number }>) {
  const expected = devices.filter((item) => item.expected).length;
  const ready = devices.filter((item) => item.expected && item.readyChannels > 0).length;
  return { expected, ready };
}

test('grouped readiness tolerates individual no-signal channels when every DVR has live media', () => {
  const result = groupedReady([
    { id: 'dvr-a', expected: true, readyChannels: 7 },
    { id: 'dvr-b', expected: true, readyChannels: 6 }
  ]);
  assert.deepEqual(result, { expected: 2, ready: 2 });
});

test('grouped readiness still fails when an enabled DVR has no live media at all', () => {
  const result = groupedReady([
    { id: 'dvr-a', expected: true, readyChannels: 7 },
    { id: 'dvr-b', expected: true, readyChannels: 0 }
  ]);
  assert.deepEqual(result, { expected: 2, ready: 1 });
});
