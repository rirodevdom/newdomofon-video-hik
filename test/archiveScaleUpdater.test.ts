import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';

const updater = fs.readFileSync('scripts/update-installed-project.sh', 'utf8');
const envExample = fs.readFileSync('deploy/env/app.env.example', 'utf8');
const pkg = JSON.parse(fs.readFileSync('package.json', 'utf8')) as { scripts: { prebuild: string } };

test('production updater migrates the old managed archive limit from 4 to 48', () => {
  assert.match(updater, /migrate_env_default HIK_DEVICE_ARCHIVE_MAX_ACTIVE_PER_DVR 4 48/);
  assert.match(updater, /set_env_default HIK_DEVICE_ARCHIVE_WORKER_COUNT 3/);
  assert.match(updater, /set_env_default HIK_DEVICE_ARCHIVE_MAX_ACTIVE_PER_WORKER 16/);
  assert.match(updater, /set_env_default HIK_SMARTYARD_ARCHIVE_MAX_BURSTS_PER_DVR 48/);
});

test('archive scale defaults are documented and materialized last', () => {
  assert.match(envExample, /HIK_DEVICE_ARCHIVE_MAX_ACTIVE_PER_DVR=48/);
  assert.match(envExample, /HIK_DEVICE_ARCHIVE_WORKER_COUNT=3/);
  assert.match(envExample, /HIK_DEVICE_ARCHIVE_MAX_ACTIVE_PER_WORKER=16/);
  assert.match(envExample, /HIK_SMARTYARD_ARCHIVE_MAX_BURSTS_PER_DVR=48/);
  assert.match(pkg.scripts.prebuild, /patch-native-archive-worker-pool\.py --project-dir \.$/);
});
