#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

WORKER_MARKER = 'HIK_SDK_OUTPUT'
CLIENT_MARKER = 'downloadNativeArchiveRange'
MEDIA_MARKER = 'smartyard-virtual-archive-segment'


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if new in text:
        return text
    count = text.count(old)
    if count != 1:
        raise SystemExit(f'{label}: expected one source block, found {count}')
    return text.replace(old, new, 1)


def patch_worker(path: Path) -> None:
    text = path.read_text(encoding='utf-8')
    if WORKER_MARKER in text and 'mode_download' in text:
        print('HCNetSDK short archive download worker already prepared')
        return

    download_mode = r'''
void mode_download(SdkSession& sdk) {
  NET_DVR_PLAYCOND cond{};
  cond.dwChannel = static_cast<DWORD>(env_int("HIK_SDK_CHANNEL", 1));
  cond.struStartTime = parse_iso_utc(env_required("HIK_SDK_START"));
  cond.struStopTime = parse_iso_utc(env_required("HIK_SDK_END"));
  cond.byStreamType = static_cast<BYTE>(logical_stream_type());

  const std::string output = env_required("HIK_SDK_OUTPUT");
  LONG handle = NET_DVR_GetFileByTime_V40(
      sdk.user_id(), const_cast<char*>(output.c_str()), &cond);
  if (handle < 0) sdk.fail("NET_DVR_GetFileByTime_V40");

  DWORD offset = 0;
  DWORD outLen = 0;
  if (!NET_DVR_PlayBackControl_V40(
          handle, NET_DVR_PLAYSTART, &offset, sizeof(offset), nullptr, &outLen)) {
    NET_DVR_StopGetFile(handle);
    sdk.fail("NET_DVR_DOWNLOAD_PLAYSTART");
  }

  const int timeoutMs = env_int("HIK_SDK_DOWNLOAD_TIMEOUT_MS", 30000);
  const auto deadline = std::chrono::steady_clock::now() + std::chrono::milliseconds(timeoutMs);
  bool completed = false;
  while (!g_stop.load() && std::chrono::steady_clock::now() < deadline) {
    DWORD position = 0;
    DWORD positionLen = sizeof(position);
    if (!NET_DVR_PlayBackControl_V40(
            handle, NET_DVR_PLAYGETPOS, nullptr, 0, &position, &positionLen)) {
      const DWORD error = NET_DVR_GetLastError();
      NET_DVR_StopGetFile(handle);
      std::cerr << "NET_DVR_DOWNLOAD_PLAYGETPOS failed, HCNetSDK error=" << error << "\n";
      std::exit(70);
    }
    if (position == 100) {
      completed = true;
      break;
    }
    if (position >= 200) {
      NET_DVR_StopGetFile(handle);
      std::cerr << "HCNetSDK archive download abnormal progress=" << position << "\n";
      std::exit(70);
    }
    std::this_thread::sleep_for(std::chrono::milliseconds(50));
  }
  NET_DVR_StopGetFile(handle);

  if (!completed) {
    std::cerr << "HCNetSDK archive download timeout\n";
    std::exit(70);
  }

  struct stat st{};
  if (::stat(output.c_str(), &st) != 0 || st.st_size <= 0) {
    std::cerr << "HCNetSDK archive download produced no file\n";
    std::exit(70);
  }
  std::cout << "{\"ok\":true,\"bytes\":" << static_cast<long long>(st.st_size) << "}" << std::endl;
}

'''
    text = replace_once(text, 'void mode_events(SdkSession& sdk) {', download_mode + 'void mode_events(SdkSession& sdk) {', 'download worker mode')
    text = replace_once(
        text,
        '    std::cerr << "usage: hik-sdk-worker <probe|ranges|live|playback|events>\\n";',
        '    std::cerr << "usage: hik-sdk-worker <probe|ranges|live|playback|download|events>\\n";',
        'download worker usage',
    )
    text = replace_once(
        text,
        '  else if (mode == "playback") mode_playback(sdk);\n  else if (mode == "events") mode_events(sdk);',
        '  else if (mode == "playback") mode_playback(sdk);\n  else if (mode == "download") mode_download(sdk);\n  else if (mode == "events") mode_events(sdk);',
        'download worker dispatch',
    )
    path.write_text(text, encoding='utf-8')
    print('HCNetSDK short archive download mode prepared')


def patch_client(path: Path) -> None:
    text = path.read_text(encoding='utf-8')
    if CLIENT_MARKER in text:
        print('HCNetSDK archive range download client already prepared')
        return

    helper = r'''
export async function downloadNativeArchiveRange(
  device: HikvisionDeviceConfig,
  channel: HikvisionChannel,
  start: Date,
  end: Date,
  output: string
): Promise<{ ok: boolean; bytes: number }> {
  return serializeHelper(() => runJsonExecutableNow<{ ok: boolean; bytes: number }>(
    config.nativeSdkWorker,
    ['download'],
    device,
    'HCNetSDK archive download',
    {
      HIK_SDK_CHANNEL: String(sdkChannel(channel)),
      HIK_SDK_START: start.toISOString(),
      HIK_SDK_END: end.toISOString(),
      HIK_SDK_STREAM_TYPE: '0',
      HIK_SDK_OUTPUT: output,
      HIK_SDK_DOWNLOAD_TIMEOUT_MS: '30000'
    }
  ));
}

'''
    text = replace_once(text, 'export function spawnNativeStream(\n', helper + 'export function spawnNativeStream(\n', 'archive download client helper')
    path.write_text(text, encoding='utf-8')
    print('HCNetSDK short archive download client prepared')


def patch_media_routes(path: Path) -> None:
    text = path.read_text(encoding='utf-8')
    if MEDIA_MARKER in text:
        print('SmartYard virtual archive segment route already prepared')
        return

    import_anchor = "import { DeviceArchiveSessionManager } from '../archive/deviceArchiveSessions.js';\n"
    if "downloadNativeArchiveRange" not in text:
        text = replace_once(
            text,
            import_anchor,
            import_anchor + "import { downloadNativeArchiveRange } from '../nativeSdk/client.js';\n",
            'virtual archive download import',
        )

    helpers = r'''
const SMARTYARD_VIRTUAL_ARCHIVE_SEGMENT = 'smartyard-virtual-archive-segment';
const virtualArchiveJobs = new Map<string, Promise<string>>();
const VIRTUAL_ARCHIVE_SEGMENT_MAX_SECONDS = 6;
const VIRTUAL_ARCHIVE_CACHE_MS = 15 * 60 * 1000;

async function freshVirtualSegment(file: string): Promise<boolean> {
  try {
    const stat = await fs.stat(file);
    return stat.isFile() && stat.size > 188 && Date.now() - stat.mtimeMs <= VIRTUAL_ARCHIVE_CACHE_MS;
  } catch {
    return false;
  }
}

async function runFfmpegToFile(args: string[], output: string): Promise<void> {
  const child = spawn(config.ffmpegPath, args, { stdio: ['ignore', 'ignore', 'pipe'] });
  let stderr = '';
  child.stderr?.on('data', (chunk) => { stderr = `${stderr}\n${String(chunk)}`.slice(-6000); });
  await new Promise<void>((resolve, reject) => {
    const timer = setTimeout(() => {
      child.kill('SIGKILL');
      reject(new Error('Virtual archive segment ffmpeg timeout'));
    }, 20_000);
    timer.unref?.();
    child.once('error', (error) => { clearTimeout(timer); reject(error); });
    child.once('exit', (code) => {
      clearTimeout(timer);
      if (code === 0) resolve();
      else reject(new Error(stderr.trim() || `Virtual archive segment ffmpeg exited ${code}`));
    });
  });
  const stat = await fs.stat(output);
  if (!stat.isFile() || stat.size <= 188) throw new Error('Virtual archive segment is empty');
}

async function ensureVirtualArchiveSegment(
  found: Awaited<ReturnType<typeof find>>,
  start: Date,
  duration: number
): Promise<string> {
  if (found.channel.archive_storage !== 'device') {
    throw Object.assign(new Error('Virtual archive segment is only used for device archive'), { statusCode: 409 });
  }
  const roundedDuration = Math.max(1, Math.min(VIRTUAL_ARCHIVE_SEGMENT_MAX_SECONDS, duration));
  const root = path.join(config.tempRoot, 'smartyard-virtual-archive', safeId(found.channelId));
  const key = `${start.getTime()}-${roundedDuration.toFixed(3)}`.replace(/[^0-9.-]+/g, '_');
  const output = path.join(root, `${key}.ts`);
  if (await freshVirtualSegment(output)) return output;

  const existing = virtualArchiveJobs.get(output);
  if (existing) return existing;

  const job = (async () => {
    await fs.mkdir(root, { recursive: true, mode: 0o750 });
    const raw = path.join(root, `${key}.download`);
    const temp = path.join(root, `${key}.tmp.ts`);
    await Promise.all([fs.rm(raw, { force: true }), fs.rm(temp, { force: true })]);

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
    await fs.rename(temp, output);
    await fs.rm(raw, { force: true }).catch(() => undefined);
    return output;
  })().finally(() => virtualArchiveJobs.delete(output));

  virtualArchiveJobs.set(output, job);
  return job;
}

'''
    text = replace_once(text, 'export function createMediaRouter(', helpers + 'export function createMediaRouter(', 'virtual archive helpers')

    route = r'''
  router.get('/channels/:channelId/archive/virtual-segment.ts', async (req, res) => {
    try {
      const found = await find(req, service);
      authorizeMedia(req, found.channelId, 'archive');
      const start = parseDateQuery(req.query.start, 'start');
      const duration = Number(req.query.duration || 0);
      if (!Number.isFinite(duration) || duration < 1 || duration > VIRTUAL_ARCHIVE_SEGMENT_MAX_SECONDS) {
        throw Object.assign(new Error(`duration must be between 1 and ${VIRTUAL_ARCHIVE_SEGMENT_MAX_SECONDS} seconds`), { statusCode: 400 });
      }
      const file = await ensureVirtualArchiveSegment(found, start, duration);
      res.setHeader('X-Newdomofon-Hikvision-Archive-Mode', SMARTYARD_VIRTUAL_ARCHIVE_SEGMENT);
      await serveFile(res, file);
    } catch (error) {
      sendError(res, error);
    }
  });

'''
    text = replace_once(text, '  return router;\n}', route + '  return router;\n}', 'virtual archive segment route')
    path.write_text(text, encoding='utf-8')
    print('SmartYard on-demand virtual archive segment route prepared')


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--project-dir', default='.')
    parser.add_argument('--native-only', action='store_true')
    args = parser.parse_args()
    root = Path(args.project_dir).resolve()
    patch_worker(root / 'native-sdk/hik_sdk_worker.cpp')
    if not args.native_only:
        patch_client(root / 'src/nativeSdk/client.ts')
        patch_media_routes(root / 'src/http/mediaRoutes.ts')


if __name__ == '__main__':
    main()
