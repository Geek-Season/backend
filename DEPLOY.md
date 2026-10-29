# Деплой

Схема: **API, бот и воркер — на [Netrun](https://netrun.io)** (по `docker-compose.yml` из этого репозитория),
**сайт — на GitHub Pages** (репозиторий [frontend](https://github.com/Geek-Season/frontend), см. его README).

```
браузер ── https://geek-season.github.io/frontend/   (GitHub Pages: React-сайт)
   │
   └─ /numbers, /tasks, /files… ──► https://<проект>.netrun.io   (Netrun: api :8000)
                                          │  внутри сети Docker:
                                          ├─ db (PostgreSQL)   ├─ redis   ├─ rabbitmq
                                          ├─ bot (Telegram, long polling)
                                          └─ worker (достижения, уведомления)
```

## Что уже сделано в `docker-compose.yml` для сервера

- Наружу открыт **только `api` (порт 8000)**. База, Redis и RabbitMQ доступны лишь внутри сети Docker.
  Порты для разработки — в `docker-compose.dev.yml`, который на сервер не подключается.
- `APP_ENV=production`: API, бот и воркер **не запустятся без своего `JWT_SECRET`** (от 32 символов).
- Пароли базы и RabbitMQ берутся из `DB_PASSWORD` и `RABBITMQ_PASSWORD` (если не заданы — `geek`;
  снаружи эти сервисы всё равно недоступны).
- Uvicorn берёт адрес ученика из `X-Forwarded-For` — лимит попыток ввода кода считается для каждого
  отдельно, а не общий на прокси хостинга.
- Тома: `pgdata` (база), `redisdata`, `rabbitdata`, `uploads` (картинки и файлы к задачам).

## Netrun: новый проект

1. **Откуда взять код:** GitHub → `https://github.com/Geek-Season/backend`, ветка `main`.
2. **Какой сервис будет сайтом:** `api` (порт 8000). Не `db`!
3. **Секреты (.env).** Задай только эти — адреса базы, Redis и RabbitMQ уже прописаны в compose,
   строки с `localhost` из `.env.example` на сервере **не нужны** (удали их, если форма подставила).

   | Ключ | Значение |
   |---|---|
   | `JWT_SECRET` | `python -c "import secrets; print(secrets.token_urlsafe(48))"` |
   | `BOT_TOKEN` | токен от @BotFather — **отдельный бот для сервера** (один токен не может работать в двух местах: локальный бот и серверный будут мешать друг другу) |
   | `BOT_USERNAME` | имя этого бота без `@` |
   | `ADMIN_TELEGRAM_IDS` | твой Telegram ID (бот @userinfobot), несколько — через запятую |
   | `CORS_ORIGINS` | адрес сайта без пути: `https://geek-season.github.io` |
   | `DB_PASSWORD` | `python -c "import secrets; print(secrets.token_hex(24))"` |
   | `RABBITMQ_PASSWORD` | так же, другой |

   Пароли — из `token_hex` (только буквы и цифры): они подставляются в адрес подключения,
   и символы вроде `@`, `/`, `:` его сломали бы.

4. **Запустить.** При старте `api` сам применит миграции и заполнит справочник номеров.
5. Проверка: `https://<адрес-проекта>/` отвечает `{"message": "Geek Season работает"}`,
   Swagger — `https://<адрес-проекта>/docs`. Этот адрес нужен для сайта (переменная `API_URL` во frontend).
6. В логах должны работать все шесть сервисов: `db`, `redis`, `rabbitmq`, `api`, `bot`, `worker`.
   Бот пишет «Бот @… запущен», воркер — «Воркер запущен».

## Если что-то не так

| Симптом | Причина |
|---|---|
| `api`/`bot`/`worker` падают с «задайте свой JWT_SECRET» | Нет `JWT_SECRET` в секретах или он короче 32 символов |
| Вход по QR: «Вход через Telegram не настроен» (503) | Не задан `BOT_USERNAME` |
| Бот в логах: `TelegramConflictError` | Этот же токен запущен где-то ещё (например, у тебя на компьютере) |
| Сайт: «Failed to fetch», в консоли браузера — CORS | `CORS_ORIGINS` не совпадает с адресом сайта (без `/frontend` и без `/` в конце) |
| Не работает вход в базу после смены `DB_PASSWORD` | Пароль задаётся только при первом создании тома `pgdata`; поменять — `ALTER USER geek PASSWORD '…'` в самой базе |
| Пропали загруженные картинки после передеплоя | Хостинг не сохранил том `uploads` — проверь в настройках проекта постоянное хранилище |

## Если когда-нибудь появится свой Nginx

Ограничение на размер запроса — не меньше лимита файлов: `client_max_body_size 20m;`
(по умолчанию Nginx пропускает 1 МБ, и загрузка файлов к задачам сломается).
