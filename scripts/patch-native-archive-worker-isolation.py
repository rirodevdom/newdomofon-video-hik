#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

MARKER = 'NATIVE_ARCHIVE_WORKER_ISOLATION'


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if new in text:
        return text
    count = text.count(old)
    if count != 1:
        raise SystemExit(f'{label}: expected one source block, found {count}')
    return text.replace(old, new, 1)


def patch_client(path: Path) -> None:
    text = path.read_text(encoding='utf-8')
    if 'HIK_SDK_DEVICE_ARCHIVE_ONLY' in text:
        print('Archive-only grouped worker spawn already prepared')
        return
    old = """export function spawnNativeDeviceWorker(device: HikvisionDeviceConfig, liveConfigPath: string): ChildProcessWithoutNullStreams {
  if (!nativeSdkAvailable()) throw new Error(`HCNetSDK grouped runtime is not fully installed`);
  return spawn(config.nativeSdkDeviceWorker, [], {
    env: { ...deviceEnv(device), HIK_SDK_DEVICE_LIVE_CONFIG: liveConfigPath },
    stdio: ['pipe', 'pipe', 'pipe']
  });
}"""
    new = """export function spawnNativeDeviceWorker(
  device: HikvisionDeviceConfig,
  liveConfigPath: string,
  extra: Record<string, string> = {}
): ChildProcessWithoutNullStreams {
  if (!nativeSdkAvailable()) throw new Error(`HCNetSDK grouped runtime is not fully installed`);
  return spawn(config.nativeSdkDeviceWorker, [], {
    env: { ...deviceEnv(device), HIK_SDK_DEVICE_LIVE_CONFIG: liveConfigPath, ...extra },
    stdio: ['pipe', 'pipe', 'pipe']
  });
}"""
    text = replace_once(text, old, new, 'archive-only grouped worker spawn options')
    path.write_text(text, encoding='utf-8')


def patch_worker(path: Path) -> None:
    text = path.read_text(encoding='utf-8')
    if MARKER in text:
        print('Archive-only HCNetSDK worker mode already prepared')
        return
    old = """  if (!NET_DVR_SetDVRMessageCallBack_V50(0, alarm_callback, nullptr)) {
    std::cerr << \"NET_DVR_SetDVRMessageCallBack_V50 failed, HCNetSDK error=\" << NET_DVR_GetLastError() << \"\\n\";
  }
  NET_DVR_SETUPALARM_PARAM alarmParam{};
  alarmParam.dwSize = sizeof(alarmParam);
  alarmParam.byLevel = 1;
  alarmParam.byAlarmInfoType = 1;
  alarmParam.byRetAlarmTypeV40 = 0;
  LONG alarmHandle = NET_DVR_SetupAlarmChan_V41(sdk.user_id(), &alarmParam);
  if (alarmHandle < 0) {
    std::cerr << \"NET_DVR_SetupAlarmChan_V41 failed, HCNetSDK error=\" << NET_DVR_GetLastError() << \"\\n\";
  }
"""
    new = """  // NATIVE_ARCHIVE_WORKER_ISOLATION
  // A dedicated archive process must not duplicate realtime alarm subscriptions
  // already owned by the live worker for this DVR.
  const char* archiveOnlyRaw = std::getenv(\"HIK_SDK_DEVICE_ARCHIVE_ONLY\");
  const bool archiveOnly = archiveOnlyRaw && *archiveOnlyRaw && std::string(archiveOnlyRaw) != \"0\";
  LONG alarmHandle = -1;
  if (!archiveOnly) {
    if (!NET_DVR_SetDVRMessageCallBack_V50(0, alarm_callback, nullptr)) {
      std::cerr << \"NET_DVR_SetDVRMessageCallBack_V50 failed, HCNetSDK error=\" << NET_DVR_GetLastError() << \"\\n\";
    }
    NET_DVR_SETUPALARM_PARAM alarmParam{};
    alarmParam.dwSize = sizeof(alarmParam);
    alarmParam.byLevel = 1;
    alarmParam.byAlarmInfoType = 1;
    alarmParam.byRetAlarmTypeV40 = 0;
    alarmHandle = NET_DVR_SetupAlarmChan_V41(sdk.user_id(), &alarmParam);
    if (alarmHandle < 0) {
      std::cerr << \"NET_DVR_SetupAlarmChan_V41 failed, HCNetSDK error=\" << NET_DVR_GetLastError() << \"\\n\";
    }
  }
"""
    text = replace_once(text, old, new, 'archive-only alarm isolation')
    path.write_text(text, encoding='utf-8')


def patch_device_runtime(path: Path) -> None:
    text = path.read_text(encoding='utf-8')
    if MARKER in text:
        print('Native archive runtime isolation already prepared')
        return

    text = replace_once(
        text,
        """const runtimes = new Map<string, RegisteredRuntime>();
const pendingAcks = new Map<string, PendingAck>();""",
        """const runtimes = new Map<string, RegisteredRuntime>();
const archiveRuntimes = new Map<string, RegisteredRuntime>();
const archiveRuntimeFactories = new Map<string, () => ChildProcess>();
const archiveRestartTimers = new Map<string, NodeJS.Timeout>();
const activeGroupedPlaybackSessions = new Map<string, Set<string>>();
const MAX_GROUPED_PLAYBACKS_PER_DEVICE = 4;
const NATIVE_ARCHIVE_WORKER_ISOLATION = 'NATIVE_ARCHIVE_WORKER_ISOLATION';
const pendingAcks = new Map<string, PendingAck>();""",
        'archive runtime state',
    )

    register_start = text.find('export function registerNativeDeviceRuntime(')
    unregister_start = text.find('export function unregisterNativeDeviceRuntime(', register_start)
    runtime_start = text.find('function runtime(deviceId: string): RegisteredRuntime {', unregister_start)
    safe_start = text.find('function safeField(', runtime_start)
    if min(register_start, unregister_start, runtime_start, safe_start) < 0:
        raise SystemExit('device runtime registration blocks not found')

    replacement = r'''function attachPlaybackStatusReader(deviceId: string, child: ChildProcess): RegisteredRuntime {
  const generation = ++generationCounter;
  const registered: RegisteredRuntime = {
    child,
    generation,
    stderrBuffer: '',
    onStderr: () => undefined
  };
  registered.onStderr = (chunk) => {
    registered.stderrBuffer += String(chunk);
    for (;;) {
      const newline = registered.stderrBuffer.indexOf('\n');
      if (newline < 0) break;
      const line = registered.stderrBuffer.slice(0, newline).trim();
      registered.stderrBuffer = registered.stderrBuffer.slice(newline + 1);
      if (line) handleStatusLine(deviceId, line);
    }
    if (registered.stderrBuffer.length > 64 * 1024) {
      registered.stderrBuffer = registered.stderrBuffer.slice(-16 * 1024);
    }
  };
  child.stderr?.on('data', registered.onStderr);
  return registered;
}

function clearArchiveRuntime(deviceId: string, kill = false): void {
  const timer = archiveRestartTimers.get(deviceId);
  if (timer) clearTimeout(timer);
  archiveRestartTimers.delete(deviceId);
  const current = archiveRuntimes.get(deviceId);
  if (!current) return;
  if (current.child.stderr) current.child.stderr.off('data', current.onStderr);
  archiveRuntimes.delete(deviceId);
  if (kill && current.child.exitCode === null) current.child.kill('SIGTERM');
}

function startArchiveRuntime(deviceId: string): void {
  if (archiveRuntimes.get(deviceId)?.child.exitCode === null) return;
  const factory = archiveRuntimeFactories.get(deviceId);
  if (!factory) return;
  clearArchiveRuntime(deviceId, false);
  const child = factory();
  const registered = attachPlaybackStatusReader(deviceId, child);
  archiveRuntimes.set(deviceId, registered);
  child.stdout?.resume();
  console.log(`[hcnetsdk-archive:${deviceId}] grouped archive runtime started`);
  child.once('exit', (code, signal) => {
    const current = archiveRuntimes.get(deviceId);
    if (current?.generation !== registered.generation) return;
    if (current.child.stderr) current.child.stderr.off('data', current.onStderr);
    archiveRuntimes.delete(deviceId);
    activeGroupedPlaybackSessions.delete(deviceId);
    rejectPendingForDevice(deviceId, `Grouped HCNetSDK archive runtime stopped for device ${deviceId}`);
    if (!archiveRuntimeFactories.has(deviceId) || archiveRestartTimers.has(deviceId)) return;
    console.warn(`[hcnetsdk-archive:${deviceId}] archive runtime exited code=${code} signal=${signal}; retry in 2000 ms`);
    const timer = setTimeout(() => {
      archiveRestartTimers.delete(deviceId);
      try { startArchiveRuntime(deviceId); }
      catch (error) {
        console.error(`[hcnetsdk-archive:${deviceId}] archive runtime restart failed`, error instanceof Error ? error.message : error);
      }
    }, 2_000);
    timer.unref?.();
    archiveRestartTimers.set(deviceId, timer);
  });
}

export function registerNativeDeviceRuntime(
  deviceId: string,
  child: ChildProcess,
  archiveFactory?: () => ChildProcess
): number {
  const previous = runtimes.get(deviceId);
  if (previous?.child.stderr) previous.child.stderr.off('data', previous.onStderr);
  if (!archiveRuntimes.has(deviceId)) {
    rejectPendingForDevice(deviceId, `Grouped HCNetSDK runtime was replaced for device ${deviceId}`);
  }

  const registered = attachPlaybackStatusReader(deviceId, child);
  runtimes.set(deviceId, registered);
  if (archiveFactory) {
    archiveRuntimeFactories.set(deviceId, archiveFactory);
    startArchiveRuntime(deviceId);
  }
  return registered.generation;
}

export function unregisterNativeDeviceRuntime(deviceId: string, generation: number): void {
  const current = runtimes.get(deviceId);
  if (current?.generation !== generation) return;
  if (current.child.stderr) current.child.stderr.off('data', current.onStderr);
  runtimes.delete(deviceId);
  archiveRuntimeFactories.delete(deviceId);
  clearArchiveRuntime(deviceId, true);
  activeGroupedPlaybackSessions.delete(deviceId);
  rejectPendingForDevice(deviceId, `Grouped HCNetSDK runtime stopped for device ${deviceId}`);
}

function runtime(deviceId: string): RegisteredRuntime {
  if (archiveRuntimeFactories.has(deviceId)) {
    const archive = archiveRuntimes.get(deviceId);
    if (!archive || archive.child.exitCode !== null || archive.child.killed || !archive.child.stdin?.writable) {
      throw Object.assign(new Error(`Grouped HCNetSDK archive runtime is not ready for device ${deviceId}`), { statusCode: 503 });
    }
    return archive;
  }
  const current = runtimes.get(deviceId);
  if (!current || current.child.exitCode !== null || current.child.killed || !current.child.stdin?.writable) {
    throw Object.assign(new Error(`Grouped HCNetSDK runtime is not ready for device ${deviceId}`), { statusCode: 503 });
  }
  return current;
}

'''
    text = text[:register_start] + replacement + text[safe_start:]

    start_fn = text.find('export function startGroupedPlayback(input: {')
    stop_fn = text.find('export function stopGroupedPlayback(', start_fn)
    if start_fn < 0 or stop_fn < 0:
        raise SystemExit('grouped playback exported functions not found')
    start_block = text[start_fn:stop_fn]
    return_anchor = "  return queueGroupedPlaybackCommand(input.deviceId, ["
    if return_anchor not in start_block:
        raise SystemExit('queued grouped playback start return not found')
    start_block = start_block.replace(
        return_anchor,
        """  let active = activeGroupedPlaybackSessions.get(input.deviceId);
  if (!active) {
    active = new Set<string>();
    activeGroupedPlaybackSessions.set(input.deviceId, active);
  }
  if (!active.has(input.sessionId) && active.size >= MAX_GROUPED_PLAYBACKS_PER_DEVICE) {
    return Promise.reject(Object.assign(
      new Error(`HCNetSDK archive playback capacity reached for device ${input.deviceId}`),
      { statusCode: 429 }
    ));
  }
  active.add(input.sessionId);
  return queueGroupedPlaybackCommand(input.deviceId, [""",
        1,
    )
    tail = "  ], input.sessionId, 'start');\n}\n\n"
    if tail not in start_block:
        raise SystemExit('grouped playback start tail not found')
    start_block = start_block.replace(
        tail,
        """  ], input.sessionId, 'start').catch((error) => {
    const sessions = activeGroupedPlaybackSessions.get(input.deviceId);
    sessions?.delete(input.sessionId);
    if (sessions?.size === 0) activeGroupedPlaybackSessions.delete(input.deviceId);
    throw error;
  });
}

""",
        1,
    )
    text = text[:start_fn] + start_block + text[stop_fn:]

    old_stop = """export function stopGroupedPlayback(deviceId: string, sessionId: string): Promise<void> {
  try {
    return queueGroupedPlaybackCommand(deviceId, ['STOP_PLAYBACK', sessionId], sessionId, 'stop');
  } catch {
    return Promise.resolve();
  }
}"""
    new_stop = """export function stopGroupedPlayback(deviceId: string, sessionId: string): Promise<void> {
  const release = () => {
    const sessions = activeGroupedPlaybackSessions.get(deviceId);
    sessions?.delete(sessionId);
    if (sessions?.size === 0) activeGroupedPlaybackSessions.delete(deviceId);
  };
  try {
    return queueGroupedPlaybackCommand(deviceId, ['STOP_PLAYBACK', sessionId], sessionId, 'stop').finally(release);
  } catch {
    release();
    return Promise.resolve();
  }
}"""
    text = replace_once(text, old_stop, new_stop, 'global grouped playback capacity release')

    if MARKER not in text or 'archiveRuntimeFactories' not in text or 'MAX_GROUPED_PLAYBACKS_PER_DEVICE = 4' not in text:
        raise SystemExit('archive runtime isolation markers incomplete')
    path.write_text(text, encoding='utf-8')
    print('Archive playback now uses a dedicated persistent HCNetSDK worker per DVR')
    print('Archive worker failures no longer kill the live grouped worker')
    print('All grouped archive playback is capped at four active sessions per DVR')


def patch_recorder_manager(path: Path) -> None:
    text = path.read_text(encoding='utf-8')
    if 'HIK_SDK_DEVICE_ARCHIVE_ONLY' in text:
        print('Native recorder already provisions isolated archive worker')
        return
    old = """    const worker = spawnNativeDeviceWorker(runtime.device, runtime.configPath);
    runtime.worker = worker;
    runtime.runtimeRegistration = registerNativeDeviceRuntime(runtime.device.id, worker);
    console.log(`[hcnetsdk-device:${runtime.device.id}] grouped runtime started channels=${runtime.channels.size}`);"""
    new = """    const archiveConfigPath = path.join(path.dirname(runtime.configPath), 'archive.tsv');
    await fs.writeFile(archiveConfigPath, '', { mode: 0o600 });
    const worker = spawnNativeDeviceWorker(runtime.device, runtime.configPath);
    runtime.worker = worker;
    runtime.runtimeRegistration = registerNativeDeviceRuntime(
      runtime.device.id,
      worker,
      () => spawnNativeDeviceWorker(runtime.device, archiveConfigPath, { HIK_SDK_DEVICE_ARCHIVE_ONLY: '1' })
    );
    console.log(`[hcnetsdk-device:${runtime.device.id}] grouped runtime started channels=${runtime.channels.size}`);"""
    text = replace_once(text, old, new, 'register isolated archive worker factory')
    path.write_text(text, encoding='utf-8')


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--project-dir', default='.')
    args = parser.parse_args()
    root = Path(args.project_dir).resolve()
    patch_client(root / 'src/nativeSdk/client.ts')
    patch_worker(root / 'native-sdk/hik_sdk_device_worker.cpp')
    patch_device_runtime(root / 'src/nativeSdk/deviceRuntime.ts')
    patch_recorder_manager(root / 'src/nativeSdk/recorderManager.ts')


if __name__ == '__main__':
    main()
