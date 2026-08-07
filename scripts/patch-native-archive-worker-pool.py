#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

MARKER = 'NATIVE_ARCHIVE_WORKER_POOL'


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if new in text:
        return text
    count = text.count(old)
    if count != 1:
        raise SystemExit(f'{label}: expected one source block, found {count}')
    return text.replace(old, new, 1)


def function_bounds(text: str, signature: str) -> tuple[int, int]:
    start = text.find(signature)
    if start < 0:
        raise SystemExit(f'function not found: {signature}')
    brace = text.find('{', start)
    if brace < 0:
        raise SystemExit(f'opening brace not found: {signature}')
    depth = 0
    quote: str | None = None
    escaped = False
    for index in range(brace, len(text)):
        char = text[index]
        if quote is not None:
            if escaped:
                escaped = False
            elif char == '\\':
                escaped = True
            elif char == quote:
                quote = None
            continue
        if char in ('"', "'", '`'):
            quote = char
            continue
        if char == '{':
            depth += 1
        elif char == '}':
            depth -= 1
            if depth == 0:
                return start, index + 1
    raise SystemExit(f'closing brace not found: {signature}')


def replace_function(text: str, signature: str, replacement: str) -> str:
    start, end = function_bounds(text, signature)
    return text[:start] + replacement.rstrip() + text[end:]


def patch_config(path: Path) -> None:
    text = path.read_text(encoding='utf-8')
    if 'deviceArchiveWorkerCount' in text:
        print('Archive scale configuration already prepared')
        return
    old = "  deviceArchiveMaxActivePerDvr: numberEnv('HIK_DEVICE_ARCHIVE_MAX_ACTIVE_PER_DVR', 4, 1),"
    new = """  // Archive scale defaults: 3 isolated workers x 16 native playbacks = 48 viewers.
  // Keep a hard software ceiling of 64 so an accidental env typo cannot flood a DVR.
  deviceArchiveMaxActivePerDvr: Math.min(64, numberEnv('HIK_DEVICE_ARCHIVE_MAX_ACTIVE_PER_DVR', 48, 1)),
  deviceArchiveWorkerCount: Math.min(8, numberEnv('HIK_DEVICE_ARCHIVE_WORKER_COUNT', 3, 1)),
  deviceArchiveMaxActivePerWorker: Math.min(32, numberEnv('HIK_DEVICE_ARCHIVE_MAX_ACTIVE_PER_WORKER', 16, 1)),
  smartyardArchiveMaxBurstsPerDvr: Math.min(64, numberEnv('HIK_SMARTYARD_ARCHIVE_MAX_BURSTS_PER_DVR', 48, 1)),"""
    path.write_text(replace_once(text, old, new, 'archive scale config'), encoding='utf-8')


def patch_recorder_manager(path: Path) -> None:
    text = path.read_text(encoding='utf-8')
    if 'HIK_SDK_ARCHIVE_WORKER_INDEX' in text:
        print('Recorder already provisions indexed archive workers')
        return
    old = """    runtime.runtimeRegistration = registerNativeDeviceRuntime(
      runtime.device.id,
      worker,
      () => spawnNativeDeviceWorker(runtime.device, archiveConfigPath, { HIK_SDK_DEVICE_ARCHIVE_ONLY: '1' })
    );"""
    new = """    runtime.runtimeRegistration = registerNativeDeviceRuntime(
      runtime.device.id,
      worker,
      (workerIndex) => spawnNativeDeviceWorker(runtime.device, archiveConfigPath, {
        HIK_SDK_DEVICE_ARCHIVE_ONLY: '1',
        HIK_SDK_ARCHIVE_WORKER_INDEX: String(workerIndex),
        HIK_SDK_MAX_PLAYBACKS: String(config.deviceArchiveMaxActivePerWorker)
      })
    );"""
    path.write_text(replace_once(text, old, new, 'indexed archive worker factory'), encoding='utf-8')


def patch_device_runtime(path: Path) -> None:
    text = path.read_text(encoding='utf-8')
    if MARKER in text:
        print('Native archive worker pool already prepared')
        return

    if "import { config } from '../config.js';" not in text:
        text = replace_once(
            text,
            "import type { ChildProcess } from 'node:child_process';",
            "import type { ChildProcess } from 'node:child_process';\nimport { config } from '../config.js';",
            'device runtime config import',
        )

    old_state = """const archiveRuntimes = new Map<string, RegisteredRuntime>();
const archiveRuntimeFactories = new Map<string, () => ChildProcess>();
const archiveRestartTimers = new Map<string, NodeJS.Timeout>();
const activeGroupedPlaybackSessions = new Map<string, Set<string>>();
const MAX_GROUPED_PLAYBACKS_PER_DEVICE = 4;
const NATIVE_ARCHIVE_WORKER_ISOLATION = 'NATIVE_ARCHIVE_WORKER_ISOLATION';"""
    new_state = """const archiveRuntimes = new Map<string, Map<number, RegisteredRuntime>>();
const archiveRuntimeFactories = new Map<string, (workerIndex: number) => ChildProcess>();
const archiveRestartTimers = new Map<string, Map<number, NodeJS.Timeout>>();
const groupedPlaybackAssignments = new Map<string, Map<string, number>>();
const NATIVE_ARCHIVE_WORKER_ISOLATION = 'NATIVE_ARCHIVE_WORKER_ISOLATION';
const NATIVE_ARCHIVE_WORKER_POOL = 'NATIVE_ARCHIVE_WORKER_POOL';"""
    text = replace_once(text, old_state, new_state, 'archive worker pool state')

    clear_start = text.find('function clearArchiveRuntime(')
    register_start = text.find('export function registerNativeDeviceRuntime(', clear_start)
    if clear_start < 0 or register_start < 0:
        raise SystemExit('archive runtime lifecycle block not found')

    lifecycle = r'''function workerQueueKey(deviceId: string, workerIndex: number): string {
  return `${deviceId}|archive|${workerIndex}`;
}

function archiveRuntimeMap(deviceId: string): Map<number, RegisteredRuntime> {
  let workers = archiveRuntimes.get(deviceId);
  if (!workers) {
    workers = new Map<number, RegisteredRuntime>();
    archiveRuntimes.set(deviceId, workers);
  }
  return workers;
}

function assignmentMap(deviceId: string): Map<string, number> {
  let assignments = groupedPlaybackAssignments.get(deviceId);
  if (!assignments) {
    assignments = new Map<string, number>();
    groupedPlaybackAssignments.set(deviceId, assignments);
  }
  return assignments;
}

function restartTimerMap(deviceId: string): Map<number, NodeJS.Timeout> {
  let timers = archiveRestartTimers.get(deviceId);
  if (!timers) {
    timers = new Map<number, NodeJS.Timeout>();
    archiveRestartTimers.set(deviceId, timers);
  }
  return timers;
}

function releaseAssignmentsForWorker(deviceId: string, workerIndex: number): void {
  const assignments = groupedPlaybackAssignments.get(deviceId);
  if (!assignments) return;
  for (const [sessionId, assignedWorker] of [...assignments]) {
    if (assignedWorker === workerIndex) assignments.delete(sessionId);
  }
  if (assignments.size === 0) groupedPlaybackAssignments.delete(deviceId);
}

function rejectPendingForArchiveWorker(deviceId: string, workerIndex: number, message: string): void {
  const assignments = groupedPlaybackAssignments.get(deviceId);
  for (const [key, pending] of pendingAcks) {
    if (pending.deviceId !== deviceId) continue;
    if (assignments?.get(pending.sessionId) !== workerIndex) continue;
    clearTimeout(pending.timer);
    pendingAcks.delete(key);
    pending.reject(Object.assign(new Error(message), { statusCode: 503 }));
  }
}

function clearArchiveRuntime(deviceId: string, workerIndex: number, kill = false): void {
  const timers = archiveRestartTimers.get(deviceId);
  const timer = timers?.get(workerIndex);
  if (timer) clearTimeout(timer);
  timers?.delete(workerIndex);
  if (timers?.size === 0) archiveRestartTimers.delete(deviceId);

  const workers = archiveRuntimes.get(deviceId);
  const current = workers?.get(workerIndex);
  if (!current) return;
  if (current.child.stderr) current.child.stderr.off('data', current.onStderr);
  workers?.delete(workerIndex);
  if (workers?.size === 0) archiveRuntimes.delete(deviceId);
  groupedPlaybackCommandQueues.delete(workerQueueKey(deviceId, workerIndex));
  if (kill && current.child.exitCode === null) current.child.kill('SIGTERM');
}

function clearArchivePool(deviceId: string, kill = false): void {
  const workerIndexes = new Set<number>([
    ...Array.from(archiveRuntimes.get(deviceId)?.keys() || []),
    ...Array.from(archiveRestartTimers.get(deviceId)?.keys() || [])
  ]);
  for (const workerIndex of workerIndexes) clearArchiveRuntime(deviceId, workerIndex, kill);
  groupedPlaybackAssignments.delete(deviceId);
}

function startArchiveRuntime(deviceId: string, workerIndex: number): void {
  const current = archiveRuntimes.get(deviceId)?.get(workerIndex);
  if (current?.child.exitCode === null && !current.child.killed && current.child.stdin?.writable) return;
  const factory = archiveRuntimeFactories.get(deviceId);
  if (!factory) return;
  clearArchiveRuntime(deviceId, workerIndex, false);

  const child = factory(workerIndex);
  const registered = attachPlaybackStatusReader(deviceId, child);
  archiveRuntimeMap(deviceId).set(workerIndex, registered);
  child.stdout?.resume();
  console.log(`[hcnetsdk-archive:${deviceId}:${workerIndex}] grouped archive runtime started`);

  child.once('exit', (code, signal) => {
    const active = archiveRuntimes.get(deviceId)?.get(workerIndex);
    if (active?.generation !== registered.generation) return;
    if (active.child.stderr) active.child.stderr.off('data', active.onStderr);
    archiveRuntimes.get(deviceId)?.delete(workerIndex);
    if (archiveRuntimes.get(deviceId)?.size === 0) archiveRuntimes.delete(deviceId);
    rejectPendingForArchiveWorker(
      deviceId,
      workerIndex,
      `Grouped HCNetSDK archive worker ${workerIndex} stopped for device ${deviceId}`
    );
    releaseAssignmentsForWorker(deviceId, workerIndex);
    groupedPlaybackCommandQueues.delete(workerQueueKey(deviceId, workerIndex));

    if (!archiveRuntimeFactories.has(deviceId)) return;
    const timers = restartTimerMap(deviceId);
    if (timers.has(workerIndex)) return;
    console.warn(
      `[hcnetsdk-archive:${deviceId}:${workerIndex}] archive runtime exited code=${code} signal=${signal}; retry in 2000 ms`
    );
    const timer = setTimeout(() => {
      restartTimerMap(deviceId).delete(workerIndex);
      try { startArchiveRuntime(deviceId, workerIndex); }
      catch (error) {
        console.error(
          `[hcnetsdk-archive:${deviceId}:${workerIndex}] archive runtime restart failed`,
          error instanceof Error ? error.message : error
        );
      }
    }, 2_000);
    timer.unref?.();
    timers.set(workerIndex, timer);
  });
}

'''
    text = text[:clear_start] + lifecycle + text[register_start:]

    register_start = text.find('export function registerNativeDeviceRuntime(')
    safe_start = text.find('function safeField(', register_start)
    if register_start < 0 or safe_start < 0:
        raise SystemExit('archive runtime registration block not found')

    registration = r'''export function registerNativeDeviceRuntime(
  deviceId: string,
  child: ChildProcess,
  archiveFactory?: (workerIndex: number) => ChildProcess
): number {
  const previous = runtimes.get(deviceId);
  if (previous?.child.stderr) previous.child.stderr.off('data', previous.onStderr);
  rejectPendingForDevice(deviceId, `Grouped HCNetSDK runtime was replaced for device ${deviceId}`);

  archiveRuntimeFactories.delete(deviceId);
  clearArchivePool(deviceId, true);

  const registered = attachPlaybackStatusReader(deviceId, child);
  runtimes.set(deviceId, registered);
  if (archiveFactory) {
    archiveRuntimeFactories.set(deviceId, archiveFactory);
    for (let workerIndex = 0; workerIndex < config.deviceArchiveWorkerCount; workerIndex += 1) {
      if (workerIndex === 0) {
        startArchiveRuntime(deviceId, workerIndex);
        continue;
      }
      const timer = setTimeout(() => {
        if (archiveRuntimeFactories.get(deviceId) !== archiveFactory) return;
        try { startArchiveRuntime(deviceId, workerIndex); }
        catch (error) {
          console.error(
            `[hcnetsdk-archive:${deviceId}:${workerIndex}] initial archive runtime start failed`,
            error instanceof Error ? error.message : error
          );
        }
      }, workerIndex * 300);
      timer.unref?.();
    }
  }
  return registered.generation;
}

export function unregisterNativeDeviceRuntime(deviceId: string, generation: number): void {
  const current = runtimes.get(deviceId);
  if (current?.generation !== generation) return;
  if (current.child.stderr) current.child.stderr.off('data', current.onStderr);
  runtimes.delete(deviceId);
  archiveRuntimeFactories.delete(deviceId);
  clearArchivePool(deviceId, true);
  rejectPendingForDevice(deviceId, `Grouped HCNetSDK runtime stopped for device ${deviceId}`);
}

function liveRuntime(deviceId: string): RegisteredRuntime {
  const current = runtimes.get(deviceId);
  if (!current || current.child.exitCode !== null || current.child.killed || !current.child.stdin?.writable) {
    throw Object.assign(new Error(`Grouped HCNetSDK live runtime is not ready for device ${deviceId}`), { statusCode: 503 });
  }
  return current;
}

function archiveRuntime(deviceId: string, workerIndex: number): RegisteredRuntime {
  const current = archiveRuntimes.get(deviceId)?.get(workerIndex);
  if (!current || current.child.exitCode !== null || current.child.killed || !current.child.stdin?.writable) {
    throw Object.assign(
      new Error(`Grouped HCNetSDK archive worker ${workerIndex} is not ready for device ${deviceId}`),
      { statusCode: 503 }
    );
  }
  return current;
}

function archiveWorkerLoad(deviceId: string, workerIndex: number): number {
  let count = 0;
  for (const assignedWorker of groupedPlaybackAssignments.get(deviceId)?.values() || []) {
    if (assignedWorker === workerIndex) count += 1;
  }
  return count;
}

function selectArchiveWorker(deviceId: string, sessionId: string): number {
  const assignments = assignmentMap(deviceId);
  const existing = assignments.get(sessionId);
  if (existing !== undefined) return existing;

  if (assignments.size >= config.deviceArchiveMaxActivePerDvr) {
    throw Object.assign(
      new Error(`HCNetSDK archive playback capacity reached for device ${deviceId}; max=${config.deviceArchiveMaxActivePerDvr}`),
      { statusCode: 429 }
    );
  }

  const workers = archiveRuntimes.get(deviceId);
  const candidates = [...(workers?.keys() || [])]
    .filter((workerIndex) => {
      try { archiveRuntime(deviceId, workerIndex); }
      catch { return false; }
      return archiveWorkerLoad(deviceId, workerIndex) < config.deviceArchiveMaxActivePerWorker;
    })
    .sort((left, right) => archiveWorkerLoad(deviceId, left) - archiveWorkerLoad(deviceId, right) || left - right);

  const workerIndex = candidates[0];
  if (workerIndex === undefined) {
    const ready = workers?.size || 0;
    const warming = ready < config.deviceArchiveWorkerCount;
    throw Object.assign(
      new Error(
        warming
          ? `HCNetSDK archive worker pool is warming for device ${deviceId}; ready=${ready}/${config.deviceArchiveWorkerCount}`
          : `HCNetSDK archive worker pool is full for device ${deviceId}`
      ),
      { statusCode: warming ? 503 : 429 }
    );
  }

  assignments.set(sessionId, workerIndex);
  return workerIndex;
}

function releaseGroupedPlaybackAssignment(deviceId: string, sessionId: string): void {
  const assignments = groupedPlaybackAssignments.get(deviceId);
  assignments?.delete(sessionId);
  if (assignments?.size === 0) groupedPlaybackAssignments.delete(deviceId);
}

'''
    text = text[:register_start] + registration + text[safe_start:]

    write_replacement = r'''function writeCommandWithAck(
  deviceId: string,
  workerIndex: number,
  fields: string[],
  sessionId: string,
  operation: PlaybackOperation
): Promise<void> {
  const current = archiveRuntime(deviceId, workerIndex);
  const line = `${fields.map((value, index) => safeField(value, `command field ${index}`)).join('\t')}\n`;
  const key = ackKey(deviceId, sessionId, operation);

  const previous = pendingAcks.get(key);
  if (previous) {
    clearTimeout(previous.timer);
    pendingAcks.delete(key);
    previous.reject(Object.assign(new Error(`Grouped HCNetSDK playback ${operation} command was superseded`), { statusCode: 409 }));
  }

  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      pendingAcks.delete(key);
      if (operation === 'start' && current.child.stdin?.writable) {
        const safeSessionId = safeField(sessionId, 'timeout cleanup session');
        current.child.stdin.write(`STOP_PLAYBACK\t${safeSessionId}\n`);
      } else if (operation === 'stop' && current.child.exitCode === null) {
        console.error(
          `[hcnetsdk-archive:${deviceId}:${workerIndex}] grouped playback STOP acknowledgement timed out; restarting archive worker session=${sessionId}`
        );
        current.child.kill('SIGKILL');
      }
      reject(Object.assign(
        new Error(`Grouped HCNetSDK playback ${operation} acknowledgement timed out for session ${sessionId}`),
        { statusCode: 503 }
      ));
    }, 10_000);
    timer.unref?.();

    pendingAcks.set(key, { deviceId, sessionId, operation, resolve, reject, timer });

    current.child.stdin!.write(line, (error) => {
      if (!error) return;
      const pending = pendingAcks.get(key);
      if (!pending) return;
      clearTimeout(pending.timer);
      pendingAcks.delete(key);
      pending.reject(Object.assign(error, { statusCode: 503 }));
    });
  });
}'''
    text = replace_function(text, 'function writeCommandWithAck(', write_replacement)

    queue_replacement = r'''function queueGroupedPlaybackCommand(
  deviceId: string,
  workerIndex: number,
  fields: string[],
  sessionId: string,
  operation: PlaybackOperation
): Promise<void> {
  const queueKey = workerQueueKey(deviceId, workerIndex);
  const previous = groupedPlaybackCommandQueues.get(queueKey) || Promise.resolve();
  const run = previous.catch(() => undefined).then(() => (
    writeCommandWithAck(deviceId, workerIndex, fields, sessionId, operation)
  ));
  const tail = run.then(() => undefined, () => undefined);
  groupedPlaybackCommandQueues.set(queueKey, tail);
  void tail.finally(() => {
    if (groupedPlaybackCommandQueues.get(queueKey) === tail) {
      groupedPlaybackCommandQueues.delete(queueKey);
    }
  });
  return run;
}'''
    text = replace_function(text, 'function queueGroupedPlaybackCommand(', queue_replacement)

    start_replacement = r'''export function startGroupedPlayback(input: {
  deviceId: string;
  sessionId: string;
  sdkChannel: number;
  start: Date;
  end: Date;
  fifoPath: string;
  fastSteps?: number;
}): Promise<void> {
  if (!Number.isInteger(input.sdkChannel) || input.sdkChannel <= 0) {
    return Promise.reject(new Error(`Invalid HCNetSDK playback channel ${input.sdkChannel}`));
  }

  let workerIndex: number;
  try {
    workerIndex = selectArchiveWorker(input.deviceId, input.sessionId);
  } catch (error) {
    return Promise.reject(error);
  }

  return queueGroupedPlaybackCommand(input.deviceId, workerIndex, [
    'PLAYBACK',
    input.sessionId,
    String(input.sdkChannel),
    input.start.toISOString(),
    input.end.toISOString(),
    input.fifoPath,
    String(Math.max(0, Math.min(3, Math.trunc(Number(input.fastSteps || 0)))))
  ], input.sessionId, 'start').catch((error) => {
    releaseGroupedPlaybackAssignment(input.deviceId, input.sessionId);
    throw error;
  });
}'''
    text = replace_function(text, 'export function startGroupedPlayback(', start_replacement)

    stop_replacement = r'''export function stopGroupedPlayback(deviceId: string, sessionId: string): Promise<void> {
  const workerIndex = groupedPlaybackAssignments.get(deviceId)?.get(sessionId);
  if (workerIndex === undefined) return Promise.resolve();
  const release = () => releaseGroupedPlaybackAssignment(deviceId, sessionId);
  try {
    return queueGroupedPlaybackCommand(
      deviceId,
      workerIndex,
      ['STOP_PLAYBACK', sessionId],
      sessionId,
      'stop'
    ).finally(release);
  } catch {
    release();
    return Promise.resolve();
  }
}'''
    text = replace_function(text, 'export function stopGroupedPlayback(', stop_replacement)

    text = replace_once(
        text,
        '    current = runtime(input.deviceId);',
        '    current = liveRuntime(input.deviceId);',
        'event scans stay on live worker',
    )

    if MARKER not in text or 'groupedPlaybackAssignments' not in text or 'workerQueueKey' not in text:
        raise SystemExit('Native archive worker pool markers are incomplete')
    path.write_text(text, encoding='utf-8')
    print('Native archive playback now uses a sharded worker pool per DVR')
    print('Default capacity is 48 archive sessions: 3 workers x 16 playback handles')
    print('START/STOP command queues are isolated per archive worker; live runtime stays separate')


def patch_media_routes(path: Path) -> None:
    text = path.read_text(encoding='utf-8')
    if 'config.smartyardArchiveMaxBurstsPerDvr' in text:
        print('SmartYard burst capacity already follows archive scale config')
        return
    old = 'const VIRTUAL_ARCHIVE_MAX_BURSTS_PER_DEVICE = 2;'
    new = 'const VIRTUAL_ARCHIVE_MAX_BURSTS_PER_DEVICE = config.smartyardArchiveMaxBurstsPerDvr;'
    path.write_text(replace_once(text, old, new, 'SmartYard burst scale config'), encoding='utf-8')


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--project-dir', default='.')
    args = parser.parse_args()
    root = Path(args.project_dir).resolve()
    patch_config(root / 'src/config.ts')
    patch_recorder_manager(root / 'src/nativeSdk/recorderManager.ts')
    patch_device_runtime(root / 'src/nativeSdk/deviceRuntime.ts')
    patch_media_routes(root / 'src/http/mediaRoutes.ts')


if __name__ == '__main__':
    main()
