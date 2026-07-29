# Hikvision Node Contract v1

## 1. Граница

Master владеет пользователями, RBAC, устройствами как объектами управления, назначением Hikvision-node и внешними ссылками. Hikvision-node владеет ISAPI-сессиями, discovery, live-процессами и доступом к архиву Hikvision.

Master не вызывает ISAPI устройства напрямую и не хранит ISAPI runtime-код.

## 2. Аутентификация

Control API:

```http
Authorization: Bearer <HIK_NODE_TOKEN>
```

Media API использует HMAC token вида:

```text
base64url(JSON payload).base64url(HMAC-SHA256)
```

Payload:

```json
{
  "channel_id": "nvr-1:1",
  "scopes": ["live", "archive", "snapshot"],
  "iat": 1785310000,
  "exp": 1785310300
}
```

Master и node используют одинаковый `HIK_NODE_MEDIA_SECRET`. Максимальный TTL ограничивается node.

## 3. Upsert устройства

```http
PUT /api/v1/control/devices/{deviceId}
Content-Type: application/json
```

```json
{
  "name": "Основной NVR",
  "host": "10.110.56.20",
  "scheme": "http",
  "isapi_port": 80,
  "rtsp_port": 554,
  "username": "admin",
  "password": "secret",
  "archive_storage": "device",
  "retention_days": 30,
  "enabled": true,
  "reject_unauthorized_tls": true,
  "channel_overrides": {
    "1": {
      "archive_storage": "node",
      "retention_days": 14,
      "primary_stream_id": "101"
    }
  }
}
```

Node выполняет discovery синхронно. Даже если устройство временно недоступно, конфигурация сохраняется в зашифрованном state; HTTP-ответ содержит ошибку синхронизации.

## 4. Автоматический discovery

Node возвращает физические каналы. Один канал содержит несколько потоков:

```json
{
  "id": "nvr-1:1",
  "physical_channel": 1,
  "name": "Вход",
  "online": true,
  "enabled": true,
  "primary_stream_id": "101",
  "archive_track_ids": ["101", "102", "1"],
  "archive_storage": "device",
  "retention_days": 30,
  "streams": [
    {
      "id": "101",
      "stream_type": "main",
      "video_codec": "H.265",
      "width": 2560,
      "height": 1440,
      "frame_rate": 25,
      "bitrate_kbps": 4096,
      "gop": 50,
      "audio_codec": "G.711ULAW"
    }
  ]
}
```

Master сохраняет `channel.id` как внешний идентификатор интеграции. `primary_stream_id` может измениться только после новой синхронизации или явного override.

## 5. Live

Master создаёт media token со scope `live` и проксирует:

```text
/api/v1/media/channels/{channelId}/live/index.m3u8?token=...
```

Node получает RTSP с Hikvision и отдаёт HLS. Для H.265 доступно автоматическое преобразование в H.264, управляемое `HIK_TRANSCODE_H265`.

## 6. Архив на node

При `archive_storage=node`:

- FFmpeg создаёт UTC-dated TS segments;
- `archive/ranges` сканирует только локальный archive root;
- `archive/export.mp4` собирает выбранные segments;
- retention применяется по `retention_days`.

## 7. Архив на устройстве

При `archive_storage=device`:

- node не хранит длительный архив;
- `/ISAPI/ContentMgmt/search` возвращает записи и playback URI;
- `archive/ranges` строится из найденных записей;
- `archive/export.mp4` преобразует playback RTSP в fragmented MP4;
- `archive/session` создаёт временный HLS-сеанс для web playback.

## 8. Изменения, необходимые в master

Master-адаптер должен:

1. добавить отдельный тип node `hikvision`;
2. хранить control URL, agent token и media secret Hikvision-node;
3. разрешить создание Hikvision device только с назначенной Hikvision-node;
4. добавить поля `archive_storage=node|device` и `retention_days`;
5. выполнять upsert/delete/sync через control API;
6. сохранять возвращённые каналы и их stream settings;
7. выпускать media tokens и проксировать live/archive/snapshot;
8. не выполнять ISAPI-запросы самостоятельно.

Generic `newdomofon-video-node` в этот процесс не включается.
