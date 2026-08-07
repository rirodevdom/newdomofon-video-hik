import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';

test('production updater rebuilds native workers only after prebuild materialization', () => {
  const script = fs.readFileSync('scripts/update-installed-project.sh', 'utf8');
  const checkIndex = script.indexOf('npm run check');
  const rebuildLabelIndex = script.indexOf('Rebuilding installed native HCNetSDK workers after materialization');
  const rebuildCalls = script.match(/rebuild-hcnet-sdk-worker\.sh/g) || [];

  assert.ok(checkIndex >= 0, 'updater must run npm check/prebuild');
  assert.ok(rebuildLabelIndex > checkIndex, 'native worker rebuild must happen after npm prebuild materializes C++ patches');
  assert.equal(rebuildCalls.length, 1, 'updater must not install a stale native worker before materialization');
});
