#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if new in text:
        return text
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one source block, found {count}")
    return text.replace(old, new, 1)


def patch_config(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    old = """  deviceArchiveSessionSeconds: numberEnv('HIK_DEVICE_ARCHIVE_SESSION_SECONDS', 300, 10),
  deviceArchiveSessionKeepMs: numberEnv('HIK_DEVICE_ARCHIVE_SESSION_KEEP_MS', 900_000, 60_000),"""
    new = """  deviceArchiveSessionSeconds: numberEnv('HIK_DEVICE_ARCHIVE_SESSION_SECONDS', 300, 10),
  deviceArchiveSessionKeepMs: numberEnv('HIK_DEVICE_ARCHIVE_SESSION_KEEP_MS', 900_000, 60_000),
  deviceArchiveMaxActivePerDvr: numberEnv('HIK_DEVICE_ARCHIVE_MAX_ACTIVE_PER_DVR', 4, 1),"""
    path.write_text(replace_once(text, old, new, "archive playback pool config"), encoding="utf-8")


def patch_client(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    old = """    env: { ...deviceEnv(device), HIK_SDK_DEVICE_LIVE_CONFIG: liveConfigPath },"""
    new = """    env: {
      ...deviceEnv(device),
      HIK_SDK_DEVICE_LIVE_CONFIG: liveConfigPath,
      HIK_SDK_MAX_PLAYBACKS: String(config.deviceArchiveMaxActivePerDvr)
    },"""
    path.write_text(replace_once(text, old, new, "grouped worker playback limit env"), encoding="utf-8")


def patch_device_runtime(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    old = """  pending.reject(Object.assign(new Error(detail), { statusCode: 502 }));"""
    new = """  pending.reject(Object.assign(new Error(detail), { statusCode: stage === 'capacity' ? 429 : 502 }));"""
    path.write_text(replace_once(text, old, new, "grouped playback capacity HTTP status"), encoding="utf-8")


def patch_archive_sessions(path: Path) -> None:
    text = path.read_text(encoding="utf-8")

    text = replace_once(
        text,
        """const RETIRED_GRACE_MS = 45_000;
const CLEANUP_INTERVAL_MS = 10_000;""",
        """const RETIRED_GRACE_MS = 45_000;
const ARCHIVE_SLOT_IDLE_MS = 30_000;
const CLEANUP_INTERVAL_MS = 10_000;""",
        "archive playback slot idle grace",
    )

    text = replace_once(
        text,
        """  private readonly sessions = new Map<string, RuntimeSession>();
  private cleanupTimer: NodeJS.Timeout | null = null;""",
        """  private readonly sessions = new Map<string, RuntimeSession>();
  private readonly deviceQueues = new Map<string, Promise<void>>();
  private cleanupTimer: NodeJS.Timeout | null = null;""",
        "archive per-device reservation queue",
    )

    old_get_or_create = """  async getOrCreate(device: HikvisionDeviceConfig, channel: HikvisionChannel, start: Date, requestedEnd: Date): Promise<NativeArchiveSession> {
    const maxEnd = new Date(start.getTime() + config.deviceArchiveSessionSeconds * 1000);
    const end = requestedEnd > maxEnd ? maxEnd : requestedEnd;
    const id = this.key(channel.id, start, end);
    const existing = this.sessions.get(id);
    if (existing && existing.status !== 'error' && existing.status !== 'cancelled' && existing.status !== 'retired') {
      existing.lastAccessAt = Date.now();
      await this.waitReady(existing);
      return existing;
    }

    await this.retireChannel(channel.id, id);

    const dir = path.join(config.tempRoot, 'native-device-archive', safeId(channel.id), id);
    await fs.rm(dir, { recursive: true, force: true });
    await fs.mkdir(dir, { recursive: true, mode: 0o750 });
    const session: RuntimeSession = {
      id, channelId: channel.id, deviceId: device.id, start, end, dir,
      playlist: path.join(dir, 'index.m3u8'),
      status: 'preparing', error: null, createdAt: Date.now(), lastAccessAt: Date.now(),
      ffmpeg: null,
      ffmpegErrors: '',
      fifoPath: path.join(dir, 'playback.ps.fifo'),
      playbackStarted: false,
      retiredAt: null
    };
    this.sessions.set(id, session);
    try {
      await this.spawn(device, channel, session);
      await this.waitReady(session);
      return session;
    } catch (error) {
      session.status = 'error';
      session.error = error instanceof Error ? error.message : String(error);
      await this.stopRuntime(session);
      const rawStatus = error && typeof error === 'object' && 'statusCode' in error
        ? Number((error as { statusCode?: unknown }).statusCode)
        : 502;
      const statusCode = Number.isInteger(rawStatus) && rawStatus >= 400 && rawStatus <= 599 ? rawStatus : 502;
      throw Object.assign(new Error(session.error), { statusCode });
    }
  }"""

    new_get_or_create = """  async getOrCreate(device: HikvisionDeviceConfig, channel: HikvisionChannel, start: Date, requestedEnd: Date): Promise<NativeArchiveSession> {
    const maxEnd = new Date(start.getTime() + config.deviceArchiveSessionSeconds * 1000);
    const end = requestedEnd > maxEnd ? maxEnd : requestedEnd;
    const id = this.key(channel.id, start, end);
    let created = false;

    const session = await this.withDeviceQueue(device.id, async () => {
      const existing = this.sessions.get(id);
      if (existing && existing.status !== 'error' && existing.status !== 'cancelled' && existing.status !== 'retired') {
        existing.lastAccessAt = Date.now();
        return existing;
      }

      await this.ensureDeviceCapacity(device.id, id);

      const dir = path.join(config.tempRoot, 'native-device-archive', safeId(channel.id), id);
      await fs.rm(dir, { recursive: true, force: true });
      await fs.mkdir(dir, { recursive: true, mode: 0o750 });
      const next: RuntimeSession = {
        id, channelId: channel.id, deviceId: device.id, start, end, dir,
        playlist: path.join(dir, 'index.m3u8'),
        status: 'preparing', error: null, createdAt: Date.now(), lastAccessAt: Date.now(),
        ffmpeg: null,
        ffmpegErrors: '',
        fifoPath: path.join(dir, 'playback.ps.fifo'),
        playbackStarted: false,
        retiredAt: null
      };
      this.sessions.set(id, next);
      created = true;
      return next;
    });

    if (!created) {
      await this.waitReady(session);
      return session;
    }

    try {
      await this.spawn(device, channel, session);
      await this.waitReady(session);
      return session;
    } catch (error) {
      session.status = 'error';
      session.error = error instanceof Error ? error.message : String(error);
      await this.stopRuntime(session);
      const rawStatus = error && typeof error === 'object' && 'statusCode' in error
        ? Number((error as { statusCode?: unknown }).statusCode)
        : 502;
      const statusCode = Number.isInteger(rawStatus) && rawStatus >= 400 && rawStatus <= 599 ? rawStatus : 502;
      throw Object.assign(new Error(session.error), { statusCode });
    }
  }"""
    text = replace_once(text, old_get_or_create, new_get_or_create, "multi-session getOrCreate")

    old_exit = """    ffmpeg.once('exit', (code, signal) => {
      if (session.status === 'retired' || session.status === 'cancelled') return;
      if (session.status === 'preparing') {
        session.status = 'error';
        session.error = session.ffmpegErrors.trim() || `archive ffmpeg exited code=${code} signal=${signal}`;
      }
    });"""
    new_exit = """    ffmpeg.once('exit', (code, signal) => {
      if (session.ffmpeg === ffmpeg) session.ffmpeg = null;
      if (session.playbackStarted) {
        session.playbackStarted = false;
        void stopGroupedPlayback(session.deviceId, session.id).catch(() => undefined);
      }
      if (session.status === 'retired' || session.status === 'cancelled') return;
      if (session.status === 'preparing') {
        session.status = 'error';
        session.error = session.ffmpegErrors.trim() || `archive ffmpeg exited code=${code} signal=${signal}`;
      }
    });"""
    text = replace_once(text, old_exit, new_exit, "release native playback when ffmpeg ends")

    old_retire = """  private async retireChannel(channelId: string, keepId: string): Promise<void> {
    for (const session of this.sessions.values()) {
      if (session.channelId !== channelId || session.id === keepId || session.status === 'retired') continue;
      session.status = 'retired';
      session.retiredAt = Date.now();
      await this.stopRuntime(session);
    }
  }"""
    new_retire = """  private async withDeviceQueue<T>(deviceId: string, task: () => Promise<T>): Promise<T> {
    const previous = this.deviceQueues.get(deviceId) || Promise.resolve();
    let release!: () => void;
    const gate = new Promise<void>((resolve) => { release = resolve; });
    const tail = previous.then(() => gate);
    this.deviceQueues.set(deviceId, tail);
    await previous;
    try {
      return await task();
    } finally {
      release();
      if (this.deviceQueues.get(deviceId) === tail) this.deviceQueues.delete(deviceId);
    }
  }

  private occupiesPlaybackSlot(session: RuntimeSession): boolean {
    return session.status === 'preparing' || session.playbackStarted;
  }

  private async retireSession(session: RuntimeSession): Promise<void> {
    if (session.status === 'retired' || session.status === 'cancelled' || session.status === 'error') return;
    session.status = 'retired';
    session.retiredAt = Date.now();
    await this.stopRuntime(session);
  }

  private async ensureDeviceCapacity(deviceId: string, keepId: string): Promise<void> {
    const now = Date.now();
    const active = [...this.sessions.values()]
      .filter((session) => session.deviceId === deviceId && session.id !== keepId && this.occupiesPlaybackSlot(session))
      .sort((left, right) => left.lastAccessAt - right.lastAccessAt || left.createdAt - right.createdAt);

    while (active.length >= config.deviceArchiveMaxActivePerDvr) {
      const candidate = active[0];
      if (!candidate || now - candidate.lastAccessAt < ARCHIVE_SLOT_IDLE_MS) {
        throw Object.assign(
          new Error(`HCNetSDK archive playback pool is full for device ${deviceId}; max=${config.deviceArchiveMaxActivePerDvr}`),
          { statusCode: 429 }
        );
      }
      active.shift();
      await this.retireSession(candidate);
    }
  }"""
    text = replace_once(text, old_retire, new_retire, "bounded per-DVR archive session pool")

    path.write_text(text, encoding="utf-8")


def patch_cpp_worker(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    if "HIK_SDK_MAX_PLAYBACKS" in text and "has_playback_for_channel" in text:
        return

    helper_anchor = """void stop_playback(SdkDevice& sdk,
                   std::map<std::string, std::unique_ptr<PlaybackSink>>& playbacks,"""
    helper = """bool has_playback_for_channel(
    const std::map<std::string, std::unique_ptr<PlaybackSink>>& playbacks,
    int sdkChannel) {
  for (const auto& item : playbacks) {
    if (item.second && item.second->sdkChannel == sdkChannel) return true;
  }
  return false;
}

void stop_playback(SdkDevice& sdk,
                   std::map<std::string, std::unique_ptr<PlaybackSink>>& playbacks,"""
    text = replace_once(text, helper_anchor, helper, "same-channel playback tracking helper")

    old_stop = """  LiveSink* pausedLive = sink.pausedLive;
  std::cerr << "HCNetSDK grouped playback stopped session=" << sessionId << "\\n";
  playbacks.erase(it);
  if (resumeLive) resume_live(sdk, pausedLive);
  if (emitStatus) emit_playback_status(sessionId, "stop", true);"""
    new_stop = """  LiveSink* pausedLive = sink.pausedLive;
  const int playbackSdkChannel = sink.sdkChannel;
  std::cerr << "HCNetSDK grouped playback stopped session=" << sessionId << "\\n";
  playbacks.erase(it);
  if (resumeLive && !has_playback_for_channel(playbacks, playbackSdkChannel)) {
    resume_live(sdk, pausedLive);
  }
  if (emitStatus) emit_playback_status(sessionId, "stop", true);"""
    text = replace_once(text, old_stop, new_stop, "resume live after last same-channel playback")

    old_start = """  stop_playback(sdk, playbacks, sessionId, true, false);

  NET_DVR_TIME start{};"""
    new_start = """  stop_playback(sdk, playbacks, sessionId, true, false);

  const int maxPlaybacks = env_int("HIK_SDK_MAX_PLAYBACKS", 4);
  if (maxPlaybacks > 0 && static_cast<int>(playbacks.size()) >= maxPlaybacks) {
    std::cerr << "HCNetSDK grouped playback rejected session=" << sessionId
              << " stage=capacity active=" << playbacks.size()
              << " max=" << maxPlaybacks << "\\n";
    emit_playback_status(sessionId, "start", false, "capacity", 0);
    return false;
  }

  NET_DVR_TIME start{};"""
    text = replace_once(text, old_start, new_start, "native worker playback capacity")

    old_resume = """    resume_live(sdk, sink->pausedLive);"""
    new_resume = """    if (!has_playback_for_channel(playbacks, sdkChannel)) resume_live(sdk, sink->pausedLive);"""
    count = text.count(old_resume)
    if count < 1:
        raise SystemExit(f"playback failure live resume: expected at least one source block, found {count}")
    text = text.replace(old_resume, new_resume)

    path.write_text(text, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-dir", default=".")
    args = parser.parse_args()
    root = Path(args.project_dir).resolve()

    patch_config(root / "src/config.ts")
    patch_client(root / "src/nativeSdk/client.ts")
    patch_device_runtime(root / "src/nativeSdk/deviceRuntime.ts")
    patch_archive_sessions(root / "src/nativeSdk/archiveSessions.ts")
    patch_cpp_worker(root / "native-sdk/hik_sdk_device_worker.cpp")

    print("Native archive playback pool prepared: up to 4 independent sessions per DVR by default")
    print("Same-channel live resumes only after the last archive playback stops")


if __name__ == "__main__":
    main()
