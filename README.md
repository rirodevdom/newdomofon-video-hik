# NewDomofon Video Hikvision Node

Специализированная node для интеграции NewDomofon Video с устройствами Hikvision через ISAPI.

Этот репозиторий является отдельным vendor-specific data plane. Код Hikvision/ISAPI не должен возвращаться в `newdomofon-video-master` или обычную `newdomofon-video-node`.

## Ответственность

Hikvision-node:

- подключается к IPC, DVR и NVR Hikvision по HTTP/HTTPS ISAPI;
- автоматически получает сведения об устройстве, список физических каналов и потоковых профилей;
- получает настройки потоков из `/ISAPI/Streaming/channels` и `/ISAPI/Streaming/channels/<ID>`;
- получает live по RTSP и публикует HLS/JPEG для master;
- поддерживает ровно одну политику архива на канал: `node` или `device`;
- при `node` пишет локальный архив и выдаёт ranges/MP4;
- при `device` ищет записи через `/ISAPI/ContentMgmt/search` и готовит HLS/MP4 из playback URI устройства;
- хранит конфигурацию устройств в зашифрованном локальном state-файле;
- принимает управляющие запросы только с control token;
- принимает media-запросы с короткоживущим HMAC token, который может выпускать master.

Hikvision-node не управляет пользователями, RBAC, внешними managed tokens и интерфейсом администратора. Эти функции принадлежат master.

## Модель каналов

Node сначала опрашивает:

1. `/ISAPI/System/deviceInfo`;
2. `/ISAPI/System/capabilities`;
3. `/ISAPI/ContentMgmt/InputProxy/channels/status`;
4. `/ISAPI/ContentMgmt/InputProxy/channels`;
5. `/ISAPI/Streaming/channels`.

Потоки группируются по физическому каналу. Основным выбирается профиль с окончанием `01`; `02` и `03` сохраняются как дополнительные профили. Для каждого профиля сохраняются доступные устройству параметры: codec, resolution, frame rate, bitrate, GOP и audio codec.

## Политика архива

`archive_storage` обязательно имеет одно из двух значений:

- `node` — live и архив сегментируются FFmpeg на Hikvision-node;
- `device` — на node хранится только скользящее live-окно, архив ищется и воспроизводится с Hikvision/NVR.

Одновременный режим `both` намеренно отсутствует.

## API v1

Health без авторизации:

```text
GET /health
```

Control API, `Authorization: Bearer <HIK_NODE_TOKEN>`:

```text
GET    /api/v1/control/devices
PUT    /api/v1/control/devices/:deviceId
DELETE /api/v1/control/devices/:deviceId
POST   /api/v1/control/devices/:deviceId/sync
GET    /api/v1/control/channels
GET    /api/v1/control/channels/:channelId
POST   /api/v1/control/channels/:channelId/streams/:streamId/refresh
GET    /api/v1/control/recorders
POST   /api/v1/control/media-token
```

Media API, query `token=<signed-media-token>` или control token для диагностики:

```text
GET  /api/v1/media/channels/:channelId/live/index.m3u8
GET  /api/v1/media/channels/:channelId/snapshot.jpg
GET  /api/v1/media/channels/:channelId/archive/ranges?start=...&end=...
GET  /api/v1/media/channels/:channelId/archive/export.mp4?start=...&end=...
POST /api/v1/media/channels/:channelId/archive/session
```

Подробный контракт: [docs/MASTER_CONTRACT_V1.md](docs/MASTER_CONTRACT_V1.md).

## Добавление устройства

```bash
curl -fsS -X PUT \
  -H "Authorization: Bearer ${HIK_NODE_TOKEN}" \
  -H 'Content-Type: application/json' \
  http://127.0.0.1:3020/api/v1/control/devices/nvr-1 \
  -d '{
    "name": "Основной NVR",
    "host": "10.110.56.20",
    "scheme": "http",
    "isapi_port": 80,
    "rtsp_port": 554,
    "username": "admin",
    "password": "REPLACE_ME",
    "archive_storage": "device",
    "retention_days": 30,
    "enabled": true,
    "reject_unauthorized_tls": true
  }'
```

После сохранения node немедленно запускает ISAPI discovery, сохраняет каналы и запускает live recorder для каждого включённого канала.

## Локальная разработка

```bash
export HIK_NODE_TOKEN="$(openssl rand -hex 32)"
export HIK_NODE_MEDIA_SECRET="$(openssl rand -hex 32)"
export HIK_NODE_STATE_KEY="$(openssl rand -hex 32)"
export HIK_NODE_ROOT="$PWD/runtime"

npm install
npm run check
npm start
```

## Production

Production: Debian 12, Node.js 22, FFmpeg, systemd. Репозиторий устанавливается из ZIP/TAR без Git на сервере.

```bash
cd /root/newdomofon-video-hik-main
bash scripts/install.sh
```

Обновление:

```bash
cd /root/newdomofon-video-hik-main
bash scripts/update-installed-project.sh
```

Пути:

```text
/opt/newdomofon-video-hik/
/etc/newdomofon-video-hik/app.env
/var/lib/newdomofon-video-hik/state.enc.json
/var/lib/newdomofon-video-hik/live/
/var/lib/newdomofon-video-hik/archive/
/var/lib/newdomofon-video-hik/tmp/
```

## Текущий этап

Репозиторий содержит первую рабочую реализацию Hikvision-node и contract v1. Для подключения к web UI и PostgreSQL требуется отдельный master-адаптер: master должен хранить устройства, выбирать `node|device`, отправлять конфигурацию в control API и проксировать media API. ISAPI-запросы при этом остаются только в этом репозитории.
