# Проверка Hikvision через native HCNetSDK без RTSP/ISAPI

Этот этап не переключает production player. Его задача — доказать на реальном Hikvision NVR/DVR, что private Device Network SDK работает для нужного устройства до замены текущего media runtime.

## Что проверяется

`native-sdk/hik_sdk_worker.cpp` использует private HCNetSDK login (`NET_DVR_Login_V40`, `byLoginMode=0`) и не формирует RTSP URL / ISAPI HTTP requests.

Режимы worker:

- `probe` — private SDK login и базовая информация о каналах;
- `ranges` — поиск записей `NET_DVR_FindFile_V40` / `NET_DVR_FindNextFile_V40`;
- `live` — кодированный live stream через callback `NET_DVR_RealPlay_V40`;
- `playback` — архив через `NET_DVR_PlayBackByTime` + `NET_DVR_SetPlayDataCallBack_V40`;
- playback worker уже содержит управление `NET_DVR_PLAYSETTIME` для следующего этапа persistent seek;
- `events` — alarm channel `NET_DVR_SetDVRMessageCallBack_V50` + `NET_DVR_SetupAlarmChan_V41`.

## Vendor SDK

HCNetSDK не хранится в этом репозитории и не скачивается updater'ом. Оператор самостоятельно принимает лицензию Hikvision, скачивает официальный **Device Network SDK_Linux64** и переносит пакет на сервер.

Для Debian x86_64 требуется Linux64 пакет.

## Установка SDK из локального файла/каталога

Из свежего распакованного репозитория:

```bash
sudo bash scripts/install-hcnet-sdk-local.sh /root/<HIKVISION_SDK_PACKAGE_OR_DIRECTORY>
```

Скрипт:

1. ищет `HCNetSDK.h` и `libhcnetsdk.so`;
2. копирует vendor runtime в `/opt/hikvision/hcnetsdk`;
3. компилирует наш worker против официального header;
4. создаёт `/opt/hikvision/hcnetsdk/bin/hik-sdk-worker`;
5. не изменяет текущий systemd service Hikvision-node.

Если `g++` отсутствует, сначала установите стандартный Debian package `build-essential` штатным способом.

## Тест проблемного регистратора

Пароль передаётся через environment и не попадает в аргументы процесса.

Пример:

```bash
export HIK_SDK_HOST='10.130.60.17'
export HIK_SDK_PORT='8000'
export HIK_SDK_USERNAME='admin'
export HIK_SDK_PASSWORD='<PASSWORD>'
export HIK_SDK_CHANNEL='7'
export HIK_SDK_START='2026-08-04T20:00:00Z'
export HIK_SDK_END='2026-08-04T20:10:00Z'

sudo -E bash scripts/test-hcnet-sdk-device.sh
```

Результаты сохраняются в:

```text
/tmp/newdomofon-hcnet-sdk-test/
  probe.json
  ranges.json
  live.ps
  playback.ps
  events.jsonl
```

## Что прислать после теста

Без пароля и других секретов:

```bash
cat /tmp/newdomofon-hcnet-sdk-test/probe.json
cat /tmp/newdomofon-hcnet-sdk-test/ranges.json
stat -c '%n %s' /tmp/newdomofon-hcnet-sdk-test/live.ps /tmp/newdomofon-hcnet-sdk-test/playback.ps
cat /tmp/newdomofon-hcnet-sdk-test/events.jsonl
```

Также нужен stderr, если любой worker вернул `HCNetSDK error=<N>`.

## Критерий перехода production на SDK

Переключаем штатную Hikvision-node только если на проблемном NVR одновременно подтверждены:

1. `probe` успешно логинится по private SDK port;
2. `ranges` возвращает записи без ISAPI;
3. `live.ps` получает ненулевой кодированный поток;
4. `playback.ps` получает ненулевой архивный поток без RTSP 453;
5. alarm channel запускается и принимает реальные события.

После этого текущий master HTTP contract можно сохранить, а внутренние ISAPI/RTSP implementation заменить SDK-backed managers.
