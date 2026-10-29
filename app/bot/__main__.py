"""Запуск бота:  python -m app.bot

Бот сам спрашивает у Telegram новые сообщения (long polling, метод getUpdates),
поэтому ему не нужен ни домен, ни публичный адрес — работает и с компьютера.

Без сторонних библиотек для Telegram: только HTTPS-запросы из app/core/telegram.py.
Так процесс занимает ~70 МБ памяти, а не ~200 МБ, как с aiogram, — на хостинге
с лимитом памяти бот с aiogram убивался при загрузке.

При запуске бот пишет в лог диагностику: память, DNS и соединение до
api.telegram.org, ответ Telegram. Если Telegram недоступен, бот не падает,
а повторяет попытку — и в логе видно, на каком шаге он застревает.
"""

import logging
import sys
import time

from app.bot.diagnostics import memory_report, network_report
from app.bot.handlers import TelegramBotApi, handle_update
from app.core import telegram
from app.core.config import settings
from app.core.db import SessionLocal
from app.core.redis import redis_client

log = logging.getLogger("bot")

POLL_TIMEOUT = 25      # секунд Telegram держит запрос getUpdates, пока нет сообщений
RETRY_SECONDS = 30     # пауза, если Telegram недоступен или токен неверный


def connect() -> dict:
    """Дождаться ответа getMe; каждая попытка — с диагностикой в логе."""
    attempt = 0
    while True:
        attempt += 1
        log.info("попытка %d: %s", attempt, memory_report())
        for line in network_report():
            log.info("попытка %d: %s", attempt, line)
        try:
            log.info("попытка %d: запрос getMe к Telegram…", attempt)
            return telegram.call("getMe", timeout=20)
        except telegram.TelegramPermanentError as error:
            log.error("попытка %d: Telegram отклонил токен — %s. Проверьте BOT_TOKEN. Повтор через %d с",
                      attempt, error, RETRY_SECONDS)
        except telegram.TelegramTemporaryError as error:
            log.error("попытка %d: Telegram недоступен — %s. Повтор через %d с", attempt, error, RETRY_SECONDS)
        time.sleep(RETRY_SECONDS)


def poll_once(api, offset: int | None) -> int | None:
    """Один запрос getUpdates и обработка полученного. Возвращает новый offset."""
    payload = {"timeout": POLL_TIMEOUT, "allowed_updates": ["message", "callback_query"]}
    if offset is not None:
        payload["offset"] = offset
    updates = telegram.call("getUpdates", payload, timeout=POLL_TIMEOUT + 10)
    for update in updates:
        offset = update["update_id"] + 1  # даже при ошибке не обрабатываем то же обновление снова
        try:
            with SessionLocal() as db:
                handle_update(api, redis_client, db, update)
        except Exception:  # noqa: BLE001 — одна ошибка не должна останавливать бота
            log.exception("ошибка при обработке обновления %s", update.get("update_id"))
    return offset


def run() -> None:
    me = connect()
    if settings.bot_username and settings.bot_username.lower() != (me.get("username") or "").lower():
        log.warning(
            "BOT_USERNAME=%s в .env не совпадает с настоящим именем бота @%s — "
            "QR-ссылки на сайте будут вести не туда.",
            settings.bot_username, me.get("username"),
        )
    # Если когда-то был включён вебхук, getUpdates с ним не работает (409) — снимаем его.
    try:
        telegram.call("deleteWebhook")
    except telegram.TelegramError as error:
        log.warning("не удалось снять вебхук: %s", error)
    log.info("Бот @%s запущен, %s", me.get("username"), memory_report())

    api = TelegramBotApi()
    offset = None
    while True:
        try:
            offset = poll_once(api, offset)
        except telegram.TelegramTemporaryError as error:
            # 409 — этот же токен опрашивает кто-то ещё (например, бот на компьютере разработчика)
            hint = " — этот же BOT_TOKEN запущен где-то ещё" if "409" in str(error) else ""
            log.warning("getUpdates: %s%s. Повтор через 5 с", error, hint)
            time.sleep(5)
        except telegram.TelegramPermanentError as error:
            log.error("getUpdates: %s. Повтор через %d с", error, RETRY_SECONDS)
            time.sleep(RETRY_SECONDS)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s [%(name)s] %(message)s")
    if not settings.bot_token:
        # Код выхода 0, чтобы Docker не перезапускал бота по кругу без токена.
        print("Не задан BOT_TOKEN в файле .env — получите токен у @BotFather. Бот не запущен.", flush=True)
        sys.exit(0)
    log.info("бот загружен, %s", memory_report())
    try:
        run()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
