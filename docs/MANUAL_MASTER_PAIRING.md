# Ручное добавление Hikvision-node на master

Hikvision-node разворачивается первой и не требует заранее созданной записи на master. Это та же модель, которая используется обычной video node:

1. оператор устанавливает node;
2. установщик создаёт UUID, agent token и media secret локально;
3. установщик сохраняет root-only registration file;
4. оператор создаёт node в интерфейсе master и переносит значения из файла;
5. после heartbeat node становится `online` и получает назначенные Hikvision-устройства.

Автоматический pairing и master-generated bootstrap credentials не используются.

## 1. Установка node

Из распакованного ZIP ветки `main`:

```bash
cd /root/newdomofon-video-hik-main
bash scripts/install.sh
```

Установщик запросит:

```text
DVR_MASTER_URL
DVR_NODE_PUBLIC_BASE_URL
DVR_NODE_INTERNAL_URL
```

Если UUID и секреты не переданы параметрами, они генерируются на node автоматически.

Неинтерактивный вариант:

```bash
cd /root/newdomofon-video-hik-main

bash scripts/install.sh \
  --master-url https://video-master.example.com \
  --public-url http://10.0.0.41:3020 \
  --internal-url http://10.0.0.41:3020 \
  --non-interactive
```

Также поддерживаются явно заданные credentials:

```text
--node-id UUID
--node-token TOKEN
--media-secret SECRET
```

## 2. Проверка до добавления на master

```bash
systemctl is-active newdomofon-video-hik.service
curl -fsS http://127.0.0.1:3020/health | jq
journalctl -u newdomofon-video-hik.service -n 150 --no-pager
```

До создания совпадающей записи на master node продолжает обслуживать локальный API. В журнале допустимы ответы `401` или `404` от node-agent API master.

## 3. Registration file

После установки создаётся:

```text
/root/newdomofon-hik-master-registration.env
```

Права:

```text
root:root 0600
```

Содержимое:

```text
NODE_KIND=hikvision
DVR_MASTER_URL=...
DVR_NODE_ID=...
DVR_NODE_TOKEN=...
DVR_NODE_MEDIA_SECRET=...
DVR_NODE_PUBLIC_BASE_URL=...
DVR_NODE_INTERNAL_URL=...
```

Просмотр:

```bash
cat /root/newdomofon-hik-master-registration.env
```

Файл содержит секреты. Не отправляйте его в общий чат, тикет или публичное хранилище.

## 4. Создание записи на master

Откройте:

```text
Администрирование → Ноды → Создать node
```

Выберите:

```text
Тип node: Hikvision node
```

Перенесите из registration file:

```text
DVR_MASTER_URL
DVR_NODE_ID
DVR_NODE_TOKEN
DVR_NODE_MEDIA_SECRET
DVR_NODE_PUBLIC_BASE_URL
DVR_NODE_INTERNAL_URL
```

Master использует UUID как `dvr_servers.id`, хранит SHA-256 хеш agent token и сохраняет media secret для внутренней подписи media tokens.

## 5. Проверка heartbeat

```bash
sleep 25

journalctl \
  -u newdomofon-video-hik.service \
  --since '-5 minutes' \
  --no-pager

curl -fsS http://127.0.0.1:3020/health | jq
```

В интерфейсе master node должна стать `online`, а `last_seen_at` — обновляться.

## 6. Назначение Hikvision-устройства

На master откройте:

```text
Устройства → Добавить
```

Укажите:

```text
Тип подключения: HIKVISION
Hikvision node: созданная node
Host/IP
ISAPI protocol и port
RTSP port
Login/password
Хранение архива: node или device
Retention
```

После сохранения master увеличивает `config_generation` и ставит node команду перезагрузки. Hikvision-node получает конфигурацию через `/api/node-agent/config`, выполняет ISAPI discovery и отправляет найденные каналы обратно через `/api/node-agent/hikvision/sync`.

Каналы не создаются вручную. Master показывает физические каналы и main/sub/third profiles, полученные с устройства.

## 7. Источник конфигурации

После подключения к master именно master является источником истины для назначенных устройств:

- добавленное на master устройство появляется на node;
- изменение credentials/archive policy передаётся node;
- снятое назначение удаляется из активной конфигурации node;
- локальный encrypted state используется как защищённый кэш для восстановления после перезапуска или временной недоступности master.

ISAPI-запросы выполняются только Hikvision-node. Master не содержит Digest client и не обращается к `/ISAPI/...` напрямую.

## 8. Ротация credentials node

Правильный порядок:

1. подготовьте новый agent token и media secret;
2. внесите их в `/etc/newdomofon-video-hik/app.env`;
3. в master выберите node → «Задать новые credentials»;
4. введите те же значения;
5. перезапустите службу:

```bash
systemctl restart newdomofon-video-hik.service
```

Значения должны совпадать посимвольно.
