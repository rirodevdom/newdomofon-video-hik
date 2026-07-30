import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

const sourceUrl = new URL('../src/http/mediaRoutes.ts', import.meta.url);

test('live-only basename segments resolve inside the physical segments directory', async () => {
  const source = await readFile(sourceUrl, 'utf8');
  assert.match(source, /archive_storage === 'device' && !relative\.includes\('\/'\)/);
  assert.match(source, /path\.join\(root, 'segments'\)/);
  assert.match(source, /await serveFile\(res, safeFile\(mediaRoot, relative\)\)/);
  assert.doesNotMatch(
    source,
    /await serveFile\(res, safeFile\(liveRoot\(channelId, found\.channel\.archive_storage\), relative\)\)/
  );
});

test('missing media files are reported as 404 instead of an internal server error', async () => {
  const source = await readFile(sourceUrl, 'utf8');
  assert.match(source, /NodeJS\.ErrnoException/);
  assert.match(source, /code === 'ENOENT'/);
  assert.match(source, /Media file not found/);
  assert.match(source, /statusCode: 404/);
});
