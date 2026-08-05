import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';

const archiveSessions = fs.readFileSync('src/nativeSdk/archiveSessions.ts', 'utf8');
const mediaRoutes = fs.readFileSync('src/http/mediaRoutes.ts', 'utf8');

test('retired archive files stay available while a client still accesses them', () => {
  assert.match(archiveSessions, /now - session\.retiredAt >= RETIRED_GRACE_MS/);
  assert.match(archiveSessions, /now - session\.lastAccessAt >= RETIRED_GRACE_MS/);
});

test('fMP4 EXT-X-MAP URI receives the node media token', () => {
  assert.match(mediaRoutes, /function appendTokenToUri/);
  assert.match(mediaRoutes, /line\.replace\(\/URI=/);
  assert.match(mediaRoutes, /appendTokenToUri\(uri, token\)/);
});
