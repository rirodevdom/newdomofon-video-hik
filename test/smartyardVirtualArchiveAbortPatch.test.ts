import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';

test('SmartYard virtual archive jobs are cancelled when the HTTP client aborts', () => {
  const patch = fs.readFileSync('scripts/patch-smartyard-virtual-archive-request-abort.py', 'utf8');
  const pkg = JSON.parse(fs.readFileSync('package.json', 'utf8')) as { scripts?: { prebuild?: string } };

  assert.match(patch, /SMARTYARD_VIRTUAL_ARCHIVE_REQUEST_ABORT/);
  assert.match(patch, /virtualArchiveCancels/);
  assert.match(patch, /AbortController/);
  assert.match(patch, /req\.once\('aborted'/);
  assert.match(patch, /stopGroupedPlayback/);
  assert.match(patch, /controller\.signal\.aborted/);
  assert.match(pkg.scripts?.prebuild || '', /patch-smartyard-virtual-archive-request-abort\.py/);
});
