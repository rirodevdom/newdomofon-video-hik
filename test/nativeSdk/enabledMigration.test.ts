import assert from 'node:assert/strict';
import test from 'node:test';
import { resolveNativeChannelEnabled } from '../../src/nativeSdk/discovery.js';

test('native SDK channels default enabled when no master override exists', () => {
  assert.equal(resolveNativeChannelEnabled(undefined), true);
});

test('native SDK channels honor an explicit master disable override', () => {
  assert.equal(resolveNativeChannelEnabled(false), false);
  assert.equal(resolveNativeChannelEnabled(true), true);
});
