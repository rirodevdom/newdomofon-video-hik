# Используемые ISAPI endpoints

## Device и capabilities

```text
GET /ISAPI/System/deviceInfo
GET /ISAPI/System/capabilities
```

## Каналы и настройки

```text
GET /ISAPI/ContentMgmt/InputProxy/channels/status
GET /ISAPI/ContentMgmt/InputProxy/channels
GET /ISAPI/Streaming/channels
GET /ISAPI/Streaming/channels/{streamId}
```

Node использует несколько источников, потому что IPC, DVR и NVR разных поколений возвращают разные части списка каналов. Результаты объединяются по физическому номеру канала.

ID потокового профиля трактуется как:

```text
physical channel * 100 + stream type
```

Типы:

```text
01 main
02 sub
03 third
```

## Live

Проверяются два совместимых варианта RTSP URI:

```text
rtsp://host:port/Streaming/channels/{streamId}
rtsp://host:port/ISAPI/Streaming/channels/{streamId}
```

## Поиск архива устройства

```text
POST /ISAPI/ContentMgmt/search
```

Запрос `CMSearchDescription` содержит search ID, track ID, временной диапазон, позицию и размер страницы. Из `searchMatchItem` читаются `startTime`, `endTime`, `trackID` и `playbackURI`.

Если устройство не возвращает playback URI, используется совместимый fallback:

```text
rtsp://host:port/Streaming/tracks/{trackId}?starttime=...&endtime=...
```

## Аутентификация

HTTP Basic используется как первый запрос. При `401` и `WWW-Authenticate: Digest ...` запрос повторяется с Digest MD5/MD5-sess и qop=auth.
