# Geek Season — backend

Серверная часть платформы подготовки к ЕГЭ по информатике: банк заданий с проверкой ответов,
вход через Telegram, статистика ученика, пробный экзамен с таймером и переводом баллов,
рейтинг, достижения и уведомления в Telegram, редактор банка задач для администратора.

**Стек:** Python 3.12, FastAPI, SQLAlchemy 2, Alembic, PostgreSQL 16, Redis 7, RabbitMQ 3.13 (pika),
aiogram 3, PyJWT, pytest.

## Конфигурация

Настройки читаются из переменных окружения или файла `.env` (шаблон — `.env.example`; сам `.env` в репозиторий не входит).

| Переменная | Назначение |
|---|---|
| `DATABASE_URL`, `TEST_DATABASE_URL` | PostgreSQL: рабочая и тестовая базы |
| `REDIS_URL` | Redis |
| `RABBITMQ_URL` | RabbitMQ; пустое значение отключает публикацию событий |
| `BOT_TOKEN`, `BOT_USERNAME` | Telegram-бот (токен от @BotFather, имя без `@`) |
| `ADMIN_TELEGRAM_IDS` | Telegram ID администраторов через запятую |
| `JWT_SECRET` | Ключ подписи токенов |
| `EXAM_DURATION_MINUTES` | Длительность пробника, по умолчанию 235 |
| `APP_ENV` | `development` или `production`; в production обязателен свой `JWT_SECRET` (от 32 символов) |
| `CORS_ORIGINS` | Адреса сайта через запятую, если сайт и API на разных адресах |
| `DB_PASSWORD`, `RABBITMQ_PASSWORD` | Пароли PostgreSQL и RabbitMQ в Docker (по умолчанию `geek`) |
| `TIMEZONE` | Часовой пояс для подсчёта дней в статистике, по умолчанию Europe/Moscow |
| `UPLOAD_DIR` | Папка для картинок и файлов к задачам, по умолчанию `uploads` (в Docker — том `uploads`) |
| `MAX_IMAGE_MB`, `MAX_ATTACHMENT_MB` | Ограничения на размер: картинка 5 МБ, файл к заданию 20 МБ |

## Запуск

Вся система в Docker. Скопируй `.env.example` в `.env` (в нём уже подключён `docker-compose.dev.yml`
с портами для разработки) и запусти:

```bash
docker compose up --build
```

Поднимаются контейнеры `db`, `redis`, `rabbitmq`, `api`, `bot`, `worker`. При старте `api` применяет
миграции и заполняет справочник номеров и стартовый набор задач (`app/seed.py`).

> `docker-compose.yml` сам по себе — серверный вариант: наружу открыт только API, и без своего
> `JWT_SECRET` ничего не запустится. Если твой `.env` создан раньше, добавь в него три первые
> строки из `.env.example` (`COMPOSE_PATH_SEPARATOR`, `COMPOSE_FILE`, `APP_ENV`).

Деплой на сервер (Netrun) — в [DEPLOY.md](DEPLOY.md).

| Адрес | Что там |
|---|---|
| <http://localhost:8000/docs> | Swagger UI |
| <http://localhost:15672> | Панель RabbitMQ (только с `docker-compose.dev.yml`) |

Для разработки инфраструктура остаётся в Docker, процессы приложения запускаются локально:

```bash
docker compose up -d db redis rabbitmq
pip install -r requirements.txt
alembic upgrade head
python -m app.seed
uvicorn app.main:app --reload    # API
python -m app.bot                # Telegram-бот
python -m app.worker             # обработчик очередей
```

## API

Полное описание — в Swagger (`/docs`) и в репозитории [docs](https://github.com/Geek-Season/docs/tree/main/api).

| Группа | Адреса |
|---|---|
| Банк заданий | `GET /numbers`, `GET /numbers/{code}/tasks`, `GET /tasks/{id}`, `POST /tasks/{id}/check` |
| Редактор (admin) | `POST /tasks`, `PATCH /tasks/{id}`, `DELETE /tasks/{id}`, `GET /tasks/{id}/admin` |
| Картинки и файлы | `POST /tasks/{id}/files` (admin), `DELETE /tasks/{id}/files/{file_id}` (admin), `GET /files/{name}` |
| Вход | `POST /auth/telegram/start`, `GET /auth/telegram/status/{key}`, `POST /auth/code`, `GET /auth/me` |
| Ученик | `GET /me/stats`, `GET /me/achievements`, `GET /me/notifications` |
| Пробник | `POST /exam/start`, `GET /exam/current`, `GET /exam/history`, `GET /exam/{id}`, `PUT /exam/{id}/answers/{position}`, `POST /exam/{id}/finish` |
| Рейтинг | `GET /rating?period=week\|all` |

Неверный ответ ученика возвращается с кодом `200` и `is_correct: false`; ошибки запроса — `401`, `403`, `404`, `409`, `422`, `429`.

## Структура

```
app/
├── main.py              сборка приложения
├── seed.py              начальные данные
├── make_admin.py        назначение администратора из командной строки
├── core/                конфигурация, PostgreSQL, Redis, события RabbitMQ, Telegram Bot API
├── bot/                 Telegram-бот (aiogram)
├── worker/              обработчик очередей RabbitMQ
└── modules/
    ├── auth/            users; QR-ключи и коды в Redis, JWT, роли
    ├── tasks/           exam_numbers, tasks, task_parts, task_files; поиск, проверка ответов, редактор, файлы
    ├── progress/        attempts; статистика, слабые номера
    ├── exam/            exam_sessions, exam_answers; вариант, таймер, баллы, шкала 2026
    ├── rating/          рейтинг в Redis sorted set
    ├── achievements/    achievements, user_achievements
    └── notifications/   notifications
alembic/versions/        миграции
tests/                   автотесты
```

Каждый модуль разделён на `models` (таблицы), `schemas` (контракты API), `service` (логика) и `router` (HTTP).

## Ключевые механизмы

**Вход через Telegram.** Сайт получает одноразовый ключ (Redis, TTL 5 минут) и показывает QR со ссылкой
`t.me/<бот>?start=<ключ>`. Бот запрашивает подтверждение «Да / Нет»; после подтверждения сайт получает JWT.
Альтернатива — 6-значный код из команды `/login`, не более 5 попыток в минуту с адреса.

**Проверка ответа.** Ответ разбивается по пробелам; числа сравниваются как числа (`0,25` = `0.25`),
текст — без учёта регистра. Задача номеров 19–21 состоит из общего условия и трёх вопросов
(`task_parts`), каждый проверяется отдельно.

**Картинки и файлы к задачам.** Администратор загружает их в редакторе. Картинки (PNG, JPEG, GIF, WebP
до 5 МБ) показываются под условием; тип проверяется по содержимому файла, SVG не принимается. Файлы к заданию
(xlsx, xls, ods, csv, txt, docx, doc, odt, pdf, zip до 20 МБ) ученик скачивает с исходным именем — как на ЕГЭ.
На диске файл лежит под случайным именем, описание — в таблице `task_files`. До 10 картинок и 10 файлов на задачу.

**Прогресс.** Каждый ответ вошедшего ученика сохраняется в `attempts`. Задача решена при наличии верной попытки.
Номер считается слабым, если в нём есть нерешённая задача или точность ниже 60 % при трёх и более ответах.

**Пробник.** Вариант — по одной случайной задаче на номер; 19, 20, 21 — отдельные позиции, полный вариант
даёт 29 первичных баллов (26 и 27 — по 2). Срок хранится в базе (`deadline_at`), после него ответы не
принимаются и пробник завершается автоматически. Первичный балл переводится в тестовый по шкале 2026 года.
Redis хранит указатель на идущий пробник и блокировку от повторного старта.

**Рейтинг.** Очко начисляется за первое решение задачи. Рейтинги за неделю и за всё время — sorted set'ы
Redis; при потере данных пересобираются из `attempts`.

**События (RabbitMQ).**

```
API ──► geek.events (topic)
   task.answered       → achievements
   exam.finished       → achievements, notifications
   achievement.granted → notifications
ошибка → <очередь>.retry (5 с) → повтор, после 3 неудач → geek.dead
```

API публикует событие и отвечает, не дожидаясь обработки. Воркер пересчитывает достижения по данным базы,
поэтому повторная доставка не создаёт дублей; уведомления уникальны по `event_id`. Временные ошибки Telegram
(сеть, 429, 5xx) повторяются, постоянные (бот заблокирован) сохраняются в `notifications.error`.

**Роли.** `student` — любой вошедший пользователь, `admin` — управление банком задач. Роль администратора
выдаётся по `ADMIN_TELEGRAM_IDS` или командой `python -m app.make_admin @username`.

## Тесты

```bash
docker compose up -d db
pytest
```

Тесты работают на отдельной базе `geek_test`, Redis заменён на fakeredis, RabbitMQ — на перехват событий.
157 тестов, включая негативные сценарии.
