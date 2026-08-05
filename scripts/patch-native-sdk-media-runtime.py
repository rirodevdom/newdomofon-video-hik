#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

MARKER = "newdomofon-hik-native-sdk-media-runtime"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if new in text:
        return text
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one source block, found {count}")
    return text.replace(old, new, 1)


def patch_media(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    if MARKER in text:
        print("Native HCNetSDK media runtime already prepared")
        return

    import_anchor = "import { DeviceArchiveSessionManager } from '../archive/deviceArchiveSessions.js';\n"
    imports = import_anchor + "import { NativeSdkArchiveSessionManager } from '../nativeSdk/archiveSessions.js';\nimport { nativeArchiveRanges, streamNativeArchiveMp4 } from '../nativeSdk/archive.js';\nimport { nativeSdkActive } from '../nativeSdk/runtime.js';\n\nconst NATIVE_SDK_MEDIA_RUNTIME = 'newdomofon-hik-native-sdk-media-runtime';\n"
    text = replace_once(text, import_anchor, imports, "native SDK media imports")
    text = replace_once(
        text,
        "export function createMediaRouter(service: HikvisionNodeService, sessions: DeviceArchiveSessionManager): Router {",
        "export function createMediaRouter(service: HikvisionNodeService, sessions: DeviceArchiveSessionManager | NativeSdkArchiveSessionManager): Router {",
        "archive session manager union",
    )

    old_ranges = """      const items = await searchDeviceArchive(found.device.config, found.channel, start, end);\n      return res.json({ source: 'device', ranges: items.map(({ start: itemStart, end: itemEnd, source }) => ({ start: itemStart, end: itemEnd, source })) });"""
    new_ranges = """      if (nativeSdkActive()) {\n        return res.json({ source: 'device', transport: 'hcnet-private-sdk', ranges: await nativeArchiveRanges(found.device.config, found.channel, start, end) });\n      }\n      const items = await searchDeviceArchive(found.device.config, found.channel, start, end);\n      return res.json({ source: 'device', ranges: items.map(({ start: itemStart, end: itemEnd, source }) => ({ start: itemStart, end: itemEnd, source })) });"""
    text = replace_once(text, old_ranges, new_ranges, "native archive ranges")

    old_export = """      } else {\n        await streamDeviceArchiveMp4(found.device.config, found.channel, start, end, res);\n      }"""
    new_export = """      } else if (nativeSdkActive()) {\n        await streamNativeArchiveMp4(found.device.config, found.channel, start, end, res);\n      } else {\n        await streamDeviceArchiveMp4(found.device.config, found.channel, start, end, res);\n      }"""
    text = replace_once(text, old_export, new_export, "native archive export")

    path.write_text(text, encoding="utf-8")
    print("Device archive ranges/sessions/export now prefer native HCNetSDK")


def patch_index(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    import_anchor = "import { DeviceArchiveSessionManager } from './archive/deviceArchiveSessions.js';\n"
    imports = import_anchor + "import { NativeSdkArchiveSessionManager } from './nativeSdk/archiveSessions.js';\nimport { nativeSdkActive } from './nativeSdk/runtime.js';\nimport { NativeSdkEventCollector } from './nativeSdk/eventCollector.js';\n"
    text = replace_once(text, import_anchor, imports, "native SDK session imports")
    text = replace_once(
        text,
        "  const sessions = new DeviceArchiveSessionManager();",
        "  const sessions = nativeSdkActive() ? new NativeSdkArchiveSessionManager() : new DeviceArchiveSessionManager();",
        "native SDK archive session selection",
    )
    text = replace_once(
        text,
        "  const eventCollector = new HikvisionEventCollector(service);",
        "  const eventCollector = nativeSdkActive() ? new NativeSdkEventCollector(service) : new HikvisionEventCollector(service);",
        "native SDK event collector selection",
    )
    text = replace_once(
        text,
        "      isapi: true,\n      master_pairing: masterAgent.enabled,",
        "      hcnetsdk: nativeSdkActive(),\n      isapi: !nativeSdkActive(),\n      transport: nativeSdkActive() ? 'hcnet-private-sdk' : 'legacy-compatibility',\n      sync_errors: service.listDevices(true).filter((item) => Boolean(item.last_sync_error)).length,\n      master_pairing: masterAgent.enabled,",
        "native SDK health transport",
    )
    path.write_text(text, encoding="utf-8")


def patch_service(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    old = """  async initialize(): Promise<void> {\n    this.state = await this.store.load();\n    await this.reconcileRecorders();"""
    new = """  async initialize(): Promise<void> {\n    this.state = await this.store.load();\n    if (nativeSdkActive()) {\n      // Refresh HCNetSDK channel numbers (notably digital channels beginning at\n      // 33 on many NVRs) before starting any live recorder processes.\n      await this.syncAll();\n    } else {\n      await this.reconcileRecorders();\n    }"""
    text = replace_once(text, old, new, "native SDK initial discovery")
    text = replace_once(
        text,
        "          result = discoverNativeHikvisionDevice(snapshot.config, snapshot.channels);",
        "          result = await discoverNativeHikvisionDevice(snapshot.config, snapshot.channels);",
        "await native SDK discovery",
    )
    path.write_text(text, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--project-dir', default='.')
    args = parser.parse_args()
    root = Path(args.project_dir).resolve()
    patch_media(root / 'src/http/mediaRoutes.ts')
    patch_index(root / 'src/index.ts')
    patch_service(root / 'src/service.ts')


if __name__ == '__main__':
    main()
