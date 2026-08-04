#!/usr/bin/env python3
from pathlib import Path
import sys

root = Path(__file__).resolve().parents[1]
worker = (root / 'native-sdk' / 'hik_sdk_worker.cpp').read_text(encoding='utf-8')
installer = (root / 'scripts' / 'install-hcnet-sdk-local.sh').read_text(encoding='utf-8')
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

for forbidden in ('/ISAPI/', 'rtsp://', '-rtsp_transport'):
    if forbidden.lower() in worker.lower():
        raise SystemExit(f'native worker contains forbidden legacy transport marker: {forbidden}')

if 'operator-supplied package' not in installer:
    raise SystemExit('SDK installer must not imply downloading or redistributing vendor binaries')
if 'No RTSP URL or ISAPI HTTP endpoint is used by this test.' not in tester:
    raise SystemExit('native SDK test contract marker is missing')

print('Native HCNetSDK source contract validated')
