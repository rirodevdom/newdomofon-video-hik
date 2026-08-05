# Native HCNetSDK runtime

When `/opt/hikvision/hcnetsdk/bin/hik-sdk-worker` is installed and executable, Hikvision-node prefers the private Hikvision Device Network SDK transport.

Native mode uses:

- `NET_DVR_Login_V40` (`byLoginMode=0`, normally service port 8000) for device login;
- `NET_DVR_RealPlay_V40` callbacks for live and node-side recording input;
- `NET_DVR_FindFile_V40` / `NET_DVR_FindNextFile_V40` for device archive ranges;
- `NET_DVR_PlayBackByTime` callbacks for device archive HLS/MP4;
- `NET_DVR_PLAYSETTIME` support in the worker for persistent seek evolution;
- `NET_DVR_SetupAlarmChan_V41` and the SDK message callback for motion/alarm events.

The external HTTP contract to master does not change. HLS and MP4 are still produced locally by FFmpeg, but FFmpeg receives encoded PS bytes through stdin from HCNetSDK and never opens a Hikvision RTSP URL in native mode.

`HIK_NATIVE_SDK_FALLBACK=false` prevents an operational SDK failure from silently reverting to legacy RTSP/ISAPI. Installations where the SDK has never been installed can still use the legacy code unless `HIK_NATIVE_SDK_REQUIRED=true` is set.

The repository does not redistribute Hikvision SDK binaries. Install the official Linux64 Device Network SDK locally once with `scripts/install-hcnet-sdk-local.sh`. Subsequent ZIP updates rebuild the worker automatically from the installed headers/runtime using `scripts/rebuild-hcnet-sdk-worker.sh`.
