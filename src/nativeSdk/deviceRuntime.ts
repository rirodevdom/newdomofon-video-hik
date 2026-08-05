import type { ChildProcess } from 'node:child_process';

interface RegisteredRuntime {
  child: ChildProcess;
  generation: number;
}

const runtimes = new Map<string, RegisteredRuntime>();
let generationCounter = 0;

export function registerNativeDeviceRuntime(deviceId: string, child: ChildProcess): number {
  const generation = ++generationCounter;
  runtimes.set(deviceId, { child, generation });
  return generation;
}

export function unregisterNativeDeviceRuntime(deviceId: string, generation: number): void {
  const current = runtimes.get(deviceId);
  if (current?.generation === generation) runtimes.delete(deviceId);
}

function runtime(deviceId: string): RegisteredRuntime {
  const current = runtimes.get(deviceId);
  if (!current || current.child.exitCode !== null || current.child.killed || !current.child.stdin?.writable) {
    throw Object.assign(new Error(`Grouped HCNetSDK runtime is not ready for device ${deviceId}`), { statusCode: 503 });
  }
  return current;
}

function safeField(value: string, label: string): string {
  if (!value || /[\t\r\n]/.test(value)) throw new Error(`Invalid grouped HCNetSDK ${label}`);
  return value;
}

function writeCommand(deviceId: string, fields: string[]): Promise<void> {
  const current = runtime(deviceId);
  const line = `${fields.map((value, index) => safeField(value, `command field ${index}`)).join('\t')}\n`;
  return new Promise((resolve, reject) => {
    current.child.stdin!.write(line, (error) => {
      if (error) reject(Object.assign(error, { statusCode: 503 }));
      else resolve();
    });
  });
}

export function startGroupedPlayback(input: {
  deviceId: string;
  sessionId: string;
  sdkChannel: number;
  start: Date;
  end: Date;
  fifoPath: string;
}): Promise<void> {
  if (!Number.isInteger(input.sdkChannel) || input.sdkChannel <= 0) {
    return Promise.reject(new Error(`Invalid HCNetSDK playback channel ${input.sdkChannel}`));
  }
  return writeCommand(input.deviceId, [
    'PLAYBACK',
    input.sessionId,
    String(input.sdkChannel),
    input.start.toISOString(),
    input.end.toISOString(),
    input.fifoPath
  ]);
}

export function stopGroupedPlayback(deviceId: string, sessionId: string): Promise<void> {
  try {
    return writeCommand(deviceId, ['STOP_PLAYBACK', sessionId]);
  } catch {
    return Promise.resolve();
  }
}
