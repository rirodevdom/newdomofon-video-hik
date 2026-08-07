#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

MARKER = 'GROUPED_PLAYBACK_ACK_QUEUE'


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if new in text:
        return text
    count = text.count(old)
    if count != 1:
        raise SystemExit(f'{label}: expected one source block, found {count}')
    return text.replace(old, new, 1)


def patch_device_runtime(path: Path) -> None:
    text = path.read_text(encoding='utf-8')
    if MARKER in text:
        print('Grouped playback ACK queue already prepared')
        return

    text = replace_once(
        text,
        "const pendingAcks = new Map<string, PendingAck>();\nlet generationCounter = 0;",
        "const pendingAcks = new Map<string, PendingAck>();\nconst groupedPlaybackCommandQueues = new Map<string, Promise<void>>();\nconst GROUPED_PLAYBACK_ACK_QUEUE = 'GROUPED_PLAYBACK_ACK_QUEUE';\nlet generationCounter = 0;",
        'grouped playback command queue marker',
    )

    old_timeout = """    const timer = setTimeout(() => {
      pendingAcks.delete(key);
      reject(Object.assign(
        new Error(`Grouped HCNetSDK playback ${operation} acknowledgement timed out for session ${sessionId}`),
        { statusCode: 503 }
      ));
    }, 5_000);"""
    new_timeout = """    const timer = setTimeout(() => {
      pendingAcks.delete(key);
      // NET_DVR_PlayBackByTime can occasionally return after our HTTP-side ACK
      // deadline. If that happens, queue a best-effort STOP behind the delayed
      // PLAYBACK command so a late native start cannot leak a playback slot.
      if (operation === 'start' && current.child.stdin?.writable) {
        const safeSessionId = safeField(sessionId, 'timeout cleanup session');
        current.child.stdin.write(`STOP_PLAYBACK\\t${safeSessionId}\\n`);
      }
      reject(Object.assign(
        new Error(`Grouped HCNetSDK playback ${operation} acknowledgement timed out for session ${sessionId}`),
        { statusCode: 503 }
      ));
    }, 10_000);"""
    text = replace_once(text, old_timeout, new_timeout, 'grouped playback ACK timeout cleanup')

    wrapper_anchor = "export function startGroupedPlayback(input: {\n"
    wrapper = """function queueGroupedPlaybackCommand(
  deviceId: string,
  fields: string[],
  sessionId: string,
  operation: PlaybackOperation
): Promise<void> {
  const previous = groupedPlaybackCommandQueues.get(deviceId) || Promise.resolve();
  const run = previous.catch(() => undefined).then(() => (
    writeCommandWithAck(deviceId, fields, sessionId, operation)
  ));
  const tail = run.then(() => undefined, () => undefined);
  groupedPlaybackCommandQueues.set(deviceId, tail);
  void tail.finally(() => {
    if (groupedPlaybackCommandQueues.get(deviceId) === tail) {
      groupedPlaybackCommandQueues.delete(deviceId);
    }
  });
  return run;
}

"""
    text = replace_once(text, wrapper_anchor, wrapper + wrapper_anchor, 'grouped playback queued command helper')

    text = replace_once(
        text,
        "  return writeCommandWithAck(input.deviceId, [",
        "  return queueGroupedPlaybackCommand(input.deviceId, [",
        'queued grouped playback start',
    )
    text = replace_once(
        text,
        "    return writeCommandWithAck(deviceId, ['STOP_PLAYBACK', sessionId], sessionId, 'stop');",
        "    return queueGroupedPlaybackCommand(deviceId, ['STOP_PLAYBACK', sessionId], sessionId, 'stop');",
        'queued grouped playback stop',
    )

    path.write_text(text, encoding='utf-8')
    print('Grouped playback commands are serialized per DVR and late starts are cleaned up')
    print('ACK timeout now begins only when the command reaches the per-DVR queue head')


def patch_media_routes(path: Path) -> None:
    text = path.read_text(encoding='utf-8')
    if 'GROUPED_PLAYBACK_ACK_TIMEOUT_FAST_FAIL' in text:
        print('SmartYard ACK-timeout fast failure already prepared')
        return

    old = """      await Promise.all([
        fs.rm(raw, { force: true }).catch(() => undefined),
        fs.rm(temp, { force: true }).catch(() => undefined)
      ]);
      try {
        let trimSeconds = 2;"""
    new = """      await Promise.all([
        fs.rm(raw, { force: true }).catch(() => undefined),
        fs.rm(temp, { force: true }).catch(() => undefined)
      ]);
      // An ACK timeout means the persistent grouped worker is still busy with
      // the command or completing a late SDK start. Do not spend another
      // 40 seconds on the known-slow download helper; let HLS retry after the
      // timeout cleanup STOP has reached the worker.
      const GROUPED_PLAYBACK_ACK_TIMEOUT_FAST_FAIL = true;
      if (groupedError instanceof Error && groupedError.message.includes('acknowledgement timed out')) {
        throw Object.assign(new Error(groupedError.message), { statusCode: 503 });
      }
      try {
        let trimSeconds = 2;"""
    text = replace_once(text, old, new, 'SmartYard grouped ACK timeout fast failure')
    path.write_text(text, encoding='utf-8')
    print('SmartYard virtual archive returns fast 503 on grouped ACK timeout instead of slow download fallback')


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--project-dir', default='.')
    args = parser.parse_args()
    root = Path(args.project_dir).resolve()
    patch_device_runtime(root / 'src/nativeSdk/deviceRuntime.ts')
    patch_media_routes(root / 'src/http/mediaRoutes.ts')


if __name__ == '__main__':
    main()
