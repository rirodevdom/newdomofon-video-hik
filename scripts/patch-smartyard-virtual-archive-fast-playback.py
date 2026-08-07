#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

MARKER = 'SMARTYARD_VIRTUAL_ARCHIVE_FAST_PLAYBACK'


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if new in text:
        return text
    count = text.count(old)
    if count != 1:
        raise SystemExit(f'{label}: expected one source block, found {count}')
    return text.replace(old, new, 1)


def patch_device_runtime(path: Path) -> None:
    text = path.read_text(encoding='utf-8')
    if 'fastSteps?: number;' in text:
        print('Grouped playback fast-step command already prepared')
        return

    text = replace_once(
        text,
        """  end: Date;
  fifoPath: string;
}): Promise<void> {""",
        """  end: Date;
  fifoPath: string;
  fastSteps?: number;
}): Promise<void> {""",
        'grouped playback fastSteps input',
    )
    text = replace_once(
        text,
        """    input.end.toISOString(),
    input.fifoPath
  ], input.sessionId, 'start');""",
        """    input.end.toISOString(),
    input.fifoPath,
    String(Math.max(0, Math.min(3, Math.trunc(Number(input.fastSteps || 0)))))
  ], input.sessionId, 'start');""",
        'grouped playback fastSteps command field',
    )
    path.write_text(text, encoding='utf-8')
    print('Grouped playback command now carries optional fast steps')


def patch_worker(path: Path) -> None:
    text = path.read_text(encoding='utf-8')
    if 'SmartYard virtual archive fast playback' in text:
        print('Grouped HCNetSDK fast playback already prepared')
        return

    text = replace_once(
        text,
        """                    const std::string& startRaw,
                    const std::string& endRaw,
                    const std::string& fifoPath) {""",
        """                    const std::string& startRaw,
                    const std::string& endRaw,
                    const std::string& fifoPath,
                    int fastSteps) {""",
        'grouped playback fastSteps worker argument',
    )

    anchor = """  std::cerr << \"HCNetSDK grouped playback started session=\" << sessionId
            << \" sdk=\" << sdkChannel << \" start=\" << startRaw << \" end=\" << endRaw << \"\\n\";"""
    replacement = """  // SmartYard virtual archive fast playback: each NET_DVR_PLAYFAST doubles delivery speed.
  const int boundedFastSteps = fastSteps < 0 ? 0 : (fastSteps > 3 ? 3 : fastSteps);
  for (int step = 0; step < boundedFastSteps; ++step) {
    DWORD fastOutLen = 0;
    if (!NET_DVR_PlayBackControl_V40(sink->handle, NET_DVR_PLAYFAST, nullptr, 0, nullptr, &fastOutLen)) {
      std::cerr << \"HCNetSDK grouped playback fast-step failed session=\" << sessionId
                << \" step=\" << (step + 1)
                << \" error=\" << NET_DVR_GetLastError() << \"\\n\";
      break;
    }
  }

  std::cerr << \"HCNetSDK grouped playback started session=\" << sessionId
            << \" sdk=\" << sdkChannel << \" start=\" << startRaw << \" end=\" << endRaw
            << \" fast_steps=\" << boundedFastSteps << \"\\n\";"""
    text = replace_once(text, anchor, replacement, 'grouped playback fast control')

    old_handler = """    if (fields[0] == \"PLAYBACK\" && fields.size() == 6) {
      int sdkChannel = 0;
      try { sdkChannel = std::stoi(fields[2]); } catch (...) { sdkChannel = 0; }
      if (sdkChannel <= 0) {
        std::cerr << \"invalid grouped playback sdk channel session=\" << fields[1] << \"\\n\";
        emit_playback_status(fields[1], \"start\", false, \"invalid_channel\", 0);
        continue;
      }
      start_playback(sdk, playbacks, sinks, fields[1], sdkChannel, fields[3], fields[4], fields[5]);
      continue;
    }"""
    new_handler = """    if (fields[0] == \"PLAYBACK\" && (fields.size() == 6 || fields.size() == 7)) {
      int sdkChannel = 0;
      int fastSteps = 0;
      try { sdkChannel = std::stoi(fields[2]); } catch (...) { sdkChannel = 0; }
      if (fields.size() == 7) {
        try { fastSteps = std::stoi(fields[6]); } catch (...) { fastSteps = 0; }
      }
      if (sdkChannel <= 0) {
        std::cerr << \"invalid grouped playback sdk channel session=\" << fields[1] << \"\\n\";
        emit_playback_status(fields[1], \"start\", false, \"invalid_channel\", 0);
        continue;
      }
      start_playback(sdk, playbacks, sinks, fields[1], sdkChannel, fields[3], fields[4], fields[5], fastSteps);
      continue;
    }"""
    text = replace_once(text, old_handler, new_handler, 'grouped playback optional fast command field')
    path.write_text(text, encoding='utf-8')
    print('Grouped HCNetSDK playback now supports bounded fast delivery')


def patch_media_routes(path: Path) -> None:
    text = path.read_text(encoding='utf-8')
    if MARKER in text:
        print('SmartYard grouped-first virtual archive already prepared')
        return

    text = replace_once(
        text,
        """      end,
      fifoPath
    });""",
        """      end,
      fifoPath,
      fastSteps: 2
    });""",
        'SmartYard grouped playback 4x speed',
    )

    start_marker = '    let primaryError: unknown = null;\n'
    rename_marker = '    await fs.rename(temp, output);\n'
    start = text.find(start_marker)
    if start < 0:
        raise SystemExit('SmartYard virtual archive fallback block start not found')
    end = text.find(rename_marker, start)
    if end < 0:
        raise SystemExit('SmartYard virtual archive fallback block end not found')

    replacement = r'''    const SMARTYARD_VIRTUAL_ARCHIVE_FAST_PLAYBACK = true;
    let groupedError: unknown = null;
    try {
      await renderVirtualSegmentViaGroupedPlayback(found, start, roundedDuration, root, key, temp);
    } catch (error) {
      groupedError = error;
      await Promise.all([
        fs.rm(raw, { force: true }).catch(() => undefined),
        fs.rm(temp, { force: true }).catch(() => undefined)
      ]);
      try {
        let trimSeconds = 2;
        let sourceStart = new Date(start.getTime() - 2000);
        let sourceEnd = new Date(start.getTime() + roundedDuration * 1000 + 1000);
        try {
          await downloadNativeArchiveRange(found.device.config, found.channel, sourceStart, sourceEnd, raw);
        } catch (firstError) {
          trimSeconds = 0;
          sourceStart = start;
          sourceEnd = new Date(start.getTime() + roundedDuration * 1000);
          await fs.rm(raw, { force: true }).catch(() => undefined);
          try {
            await downloadNativeArchiveRange(found.device.config, found.channel, sourceStart, sourceEnd, raw);
          } catch {
            throw firstError;
          }
        }

        const ffmpegArgs = [
          '-hide_banner', '-loglevel', config.logLevel,
          '-fflags', '+genpts+discardcorrupt',
          '-probesize', '2097152', '-analyzeduration', '2000000',
          '-i', raw,
          ...(trimSeconds > 0 ? ['-ss', String(trimSeconds)] : []),
          '-t', String(roundedDuration),
          '-map', '0:v:0', '-map', '0:a?',
          '-c:v', 'libx264', '-preset', 'veryfast', '-tune', 'zerolatency',
          '-g', '50', '-sc_threshold', '0',
          '-c:a', 'aac', '-b:a', '64k', '-ac', '1', '-ar', '44100',
          '-avoid_negative_ts', 'make_zero',
          '-muxdelay', '0', '-muxpreload', '0',
          '-mpegts_flags', '+resend_headers',
          '-f', 'mpegts', temp
        ];
        await runFfmpegToFile(ffmpegArgs, temp);
      } catch (downloadError) {
        const groupedMessage = groupedError instanceof Error ? groupedError.message : String(groupedError);
        const downloadMessage = downloadError instanceof Error ? downloadError.message : String(downloadError);
        throw new Error(`Virtual archive segment failed: grouped_playback=${groupedMessage}; download=${downloadMessage}`);
      }
    }
'''
    text = text[:start] + replacement + text[end:]

    if MARKER not in text or 'fastSteps: 2' not in text or 'grouped_playback=' not in text:
        raise SystemExit('SmartYard fast grouped playback markers are incomplete')
    path.write_text(text, encoding='utf-8')
    print('SmartYard virtual archive now uses 4x grouped playback first; download-by-time is fallback only')


def patch_stub(path: Path) -> None:
    text = path.read_text(encoding='utf-8')
    if 'NET_DVR_PLAYFAST' in text:
        return
    text = replace_once(
        text,
        'constexpr DWORD NET_DVR_PLAYSTART = 1;\n',
        'constexpr DWORD NET_DVR_PLAYSTART = 1;\nconstexpr DWORD NET_DVR_PLAYFAST = 5;\n',
        'native test stub PLAYFAST constant',
    )
    path.write_text(text, encoding='utf-8')


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--project-dir', default='.')
    args = parser.parse_args()
    root = Path(args.project_dir).resolve()
    patch_device_runtime(root / 'src/nativeSdk/deviceRuntime.ts')
    patch_worker(root / 'native-sdk/hik_sdk_device_worker.cpp')
    patch_media_routes(root / 'src/http/mediaRoutes.ts')
    patch_stub(root / 'test/native-sdk/HCNetSDK.h')


if __name__ == '__main__':
    main()
