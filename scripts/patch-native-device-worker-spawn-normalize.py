#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path


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
        if char in ('\"', "'", '`'):
            quote = char
            continue
        if char == '{':
            depth += 1
        elif char == '}':
            depth -= 1
            if depth == 0:
                return start, index + 1
    raise SystemExit(f'closing brace not found: {signature}')


def patch_client(path: Path) -> None:
    text = path.read_text(encoding='utf-8')
    start, end = function_bounds(text, 'export function spawnNativeDeviceWorker(')
    normalized = """export function spawnNativeDeviceWorker(device: HikvisionDeviceConfig, liveConfigPath: string): ChildProcessWithoutNullStreams {
  if (!nativeSdkAvailable()) throw new Error(`HCNetSDK grouped runtime is not fully installed`);
  return spawn(config.nativeSdkDeviceWorker, [], {
    env: { ...deviceEnv(device), HIK_SDK_DEVICE_LIVE_CONFIG: liveConfigPath },
    stdio: ['pipe', 'pipe', 'pipe']
  });
}"""
    text = text[:start] + normalized + text[end:]
    path.write_text(text, encoding='utf-8')
    print('Native grouped worker spawn normalized before archive isolation')


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--project-dir', default='.')
    args = parser.parse_args()
    patch_client(Path(args.project_dir).resolve() / 'src/nativeSdk/client.ts')


if __name__ == '__main__':
    main()
