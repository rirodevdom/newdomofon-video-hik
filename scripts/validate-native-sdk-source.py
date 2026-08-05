#!/usr/bin/env python3
from pathlib import Path

root = Path(__file__).resolve().parents[1]
worker = (root / 'native-sdk' / 'hik_sdk_worker.cpp').read_text(encoding='utf-8')
channel_probe = (root / 'native-sdk' / 'hik_sdk_channel_probe.cpp').read_text(encoding='utf-8')
installer = (root / 'scripts' / 'install-hcnet-sdk-local.sh').read_text(encoding='utf-8')
rebuild = (root / 'scripts' / 'rebuild-hcnet-sdk-worker.sh').read_text(encoding='utf-8')
verify = (root / 'scripts' / 'verify-hcnet-sdk-runtime.sh').read_text(encoding='utf-8')
updater = (root / 'scripts' / 'update-installed-project.sh').read_text(encoding='utf-8')
runtime_patch = (root / 'scripts' / 'patch-native-sdk-media-runtime.py').read_text(encoding='utf-8')
tester = (root / 'scripts' / 'test-hcnet-sdk-device.sh').read_text(encoding='utf-8')

required = [
    'NET_DVR_Login_V40',
    'login.byLoginMode = 0',
    'NET_DVR_RealPlay_V40',
    'NET_DVR_PlayBackByTime',
    'NET_DVR_PLAYSETTIME',
    'NET_DVR_FindFile_V40',
    'NET_DVR_SetupAlarmChan_V41',
    'NET_DVR_SetDVRMessageCallBack_V50',
]
missing = [marker for marker in required if marker not in worker]
if missing:
    raise SystemExit(f'missing native HCNetSDK markers: {missing}')

channel_required = [
    'NET_DVR_GET_IPPARACFG_V40',
    'NET_DVR_IPPARACFG_V40',
    'byAnalogChanEnable',
    'NET_DVR_IPCHANINFO',
    'NET_DVR_IPCHANINFO_V40',
    'configured',
    'online',
]
channel_missing = [marker for marker in channel_required if marker not in channel_probe]
if channel_missing:
    raise SystemExit(f'missing HCNetSDK channel inventory markers: {channel_missing}')

for file_name, text in (('worker', worker), ('channel probe', channel_probe)):
    for forbidden in ('/ISAPI/', 'rtsp://', '-rtsp_transport'):
        if forbidden.lower() in text.lower():
            raise SystemExit(f'native {file_name} contains forbidden legacy transport marker: {forbidden}')

if 'operator-supplied package' not in installer:
    raise SystemExit('SDK installer must not imply downloading or redistributing vendor binaries')
if 'hik-sdk-channel-probe' not in installer or 'hik-sdk-channel-probe' not in rebuild:
    raise SystemExit('channel inventory helper must be installed and rebuilt with the main worker')
for marker in (
    'set_env_default HIK_NATIVE_SDK_PREFERRED true',
    'set_env_default HIK_NATIVE_SDK_REQUIRED true',
    'set_env_default HIK_NATIVE_SDK_FALLBACK false',
):
    if marker not in updater:
        raise SystemExit(f'native-only updater migration marker missing: {marker}')
for marker in (
    'find "$SDK_ROOT" -type d -exec chmod 0755 {} +',
    'verify-hcnet-sdk-runtime.sh',
):
    if marker not in rebuild:
        raise SystemExit(f'service-account SDK readability marker missing: {marker}')
for marker in (
    'runuser -u "$SERVICE_USER"',
    'error while loading shared libraries',
):
    if marker not in verify:
        raise SystemExit(f'service-user loader verification marker missing: {marker}')
if "transport: nativeSdkActive() ? 'hcnet-private-sdk' : 'legacy-compatibility'" not in runtime_patch:
    raise SystemExit('health transport marker is missing from native runtime patch')
if 'sync_errors: service.listDevices(true)' not in runtime_patch or 'sync_errors = int(data.get("sync_errors") or 0)' not in updater:
    raise SystemExit('native sync-error readiness contract is missing')
if 'No RTSP URL or ISAPI HTTP endpoint is used by this test.' not in tester:
    raise SystemExit('native SDK test contract marker is missing')

print('Native HCNetSDK source contract validated')
