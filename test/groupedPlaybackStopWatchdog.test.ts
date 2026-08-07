import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';

test('a stuck grouped playback STOP restarts only its DVR worker and aborted virtual ffmpeg exits promptly', () => {
  const patch = fs.readFileSync('scripts/patch-grouped-playback-stop-watchdog.py', 'utf8');
  const pkg = JSON.parse(fs.readFileSync('package.json', 'utf8')) as { scripts?: { prebuild?: string } };
  const runtime = fs.readFileSync('src/nativeSdk/deviceRuntime.ts', 'utf8');
  const routes = fs.readFileSync('src/http/mediaRoutes.ts', 'utf8');

  assert.match(patch, /GROUPED_PLAYBACK_STOP_WATCHDOG/);
  assert.match(patch, /current\.child\.kill\('SIGKILL'\)/);
  assert.match(patch, /VIRTUAL_ARCHIVE_ABORTABLE_FFMPEG/);
  assert.match(pkg.scripts?.prebuild || '', /patch-grouped-playback-stop-watchdog\.py/);

  assert.match(runtime, /GROUPED_PLAYBACK_STOP_WATCHDOG/);
  assert.match(runtime, /grouped playback STOP acknowledgement timed out; restarting stuck DVR worker/);
  assert.match(runtime, /current\.child\.kill\('SIGKILL'\)/);

  assert.match(routes, /VIRTUAL_ARCHIVE_ABORTABLE_FFMPEG/);
  assert.match(routes, /runFfmpegToFile\(args: string\[\], output: string, signal\?: AbortSignal\)/);
  assert.match(routes, /\], output, signal\);/);
  assert.match(routes, /signal\?\.addEventListener\('abort', onAbort/);
});
