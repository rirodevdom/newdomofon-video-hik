import type { ChildProcess } from 'node:child_process';

type PlaybackOperation = 'start' | 'stop';

interface RegisteredRuntime {
  child: ChildProcess;
  generation: number;
  stderrBuffer: string;
  onStderr: (chunk: Buffer | string) => void;
}

interface PendingAck {
  deviceId: string;
  sessionId: string;
  operation: PlaybackOperation;
  resolve: () => void;
  reject: (error: Error & { statusCode?: number }) => void;
  timer: NodeJS.Timeout;
}

const runtimes = new Map<string, RegisteredRuntime>();
const pendingAcks = new Map<string, PendingAck>();
let generationCounter = 0;

function ackKey(deviceId: string, sessionId: string, operation: PlaybackOperation): string {
  return `${deviceId}|${sessionId}|${operation}`;
}

function rejectPendingForDevice(deviceId: string, message: string): void {
  for (const [key, pending] of pendingAcks) {
    if (pending.deviceId !== deviceId) continue;
    clearTimeout(pending.timer);
    pendingAcks.delete(key);
    pending.reject(Object.assign(new Error(message), { statusCode: 503 }));
  }
}

function handleStatusLine(deviceId: string, line: string): void {
  if (!line.startsWith('PLAYBACK_STATUS\t')) return;
  const fields = line.split('\t');
  if (fields.length < 6) return;
  const [, sessionId, operationRaw, result, stage, errorCode] = fields;
  if (!sessionId || (operationRaw !== 'start' && operationRaw !== 'stop')) return;
  const operation = operationRaw as PlaybackOperation;
  const key = ackKey(deviceId, sessionId, operation);
  const pending = pendingAcks.get(key);
  if (!pending) return;
  clearTimeout(pending.timer);
  pendingAcks.delete(key);
  if (result === 'OK') {
    pending.resolve();
    return;
  }
  const detail = [
    `Grouped HCNetSDK playback ${operation} failed`,
    stage ? `stage=${stage}` : '',
    errorCode && errorCode !== '0' ? `error=${errorCode}` : ''
  ].filter(Boolean).join(' ');
  pending.reject(Object.assign(new Error(detail), { statusCode: 502 }));
}

export function registerNativeDeviceRuntime(deviceId: string, child: ChildProcess): number {
  const previous = runtimes.get(deviceId);
  if (previous?.child.stderr) previous.child.stderr.off('data', previous.onStderr);
  rejectPendingForDevice(deviceId, `Grouped HCNetSDK runtime was replaced for device ${deviceId}`);

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
  runtimes.set(deviceId, registered);
  return generation;
}

export function unregisterNativeDeviceRuntime(deviceId: string, generation: number): void {
  const current = runtimes.get(deviceId);
  if (current?.generation !== generation) return;
  if (current.child.stderr) current.child.stderr.off('data', current.onStderr);
  runtimes.delete(deviceId);
  rejectPendingForDevice(deviceId, `Grouped HCNetSDK runtime stopped for device ${deviceId}`);
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

function writeCommandWithAck(
  deviceId: string,
  fields: string[],
  sessionId: string,
  operation: PlaybackOperation
): Promise<void> {
  const current = runtime(deviceId);
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
      reject(Object.assign(
        new Error(`Grouped HCNetSDK playback ${operation} acknowledgement timed out for session ${sessionId}`),
        { statusCode: 503 }
      ));
    }, 5_000);
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
  return writeCommandWithAck(input.deviceId, [
    'PLAYBACK',
    input.sessionId,
    String(input.sdkChannel),
    input.start.toISOString(),
    input.end.toISOString(),
    input.fifoPath
  ], input.sessionId, 'start');
}

export function stopGroupedPlayback(deviceId: string, sessionId: string): Promise<void> {
  try {
    return writeCommandWithAck(deviceId, ['STOP_PLAYBACK', sessionId], sessionId, 'stop');
  } catch {
    return Promise.resolve();
  }
}
