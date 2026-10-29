"""Запросы к Telegram Bot API (обычный HTTPS, без сторонних библиотек).

Этим пользуются и воркер (уведомления), и бот (app/bot) — поэтому боту не нужен
aiogram: тот занимает ~170 МБ памяти, а на хостинге с лимитом памяти процесс
бота с ним убивался ещё при загрузке.

Ошибки делятся на два вида:
- TelegramTemporaryError — сеть, 409, 429 «слишком часто», 5xx: стоит повторить позже;
- TelegramPermanentError — 400/401/403/404 (бот заблокирован, чат не найден,
  неверный токен): повтор не поможет.
"""

import json
import urllib.error
import urllib.request

from app.core.config import settings

PERMANENT_CODES = {400, 401, 403, 404}


class TelegramError(Exception):
    pass


class TelegramTemporaryError(TelegramError):
    pass


class TelegramPermanentError(TelegramError):
    pass


def call(method: str, payload: dict | None = None, timeout: float = 10):
    """Вызвать метод Bot API и вернуть поле result ответа.

    timeout — сколько секунд ждать ответа; для getUpdates (long polling)
    он должен быть больше, чем timeout самого запроса.
    """
    if not settings.bot_token:
        raise TelegramPermanentError("BOT_TOKEN не задан")
    request = urllib.request.Request(
        f"{settings.telegram_api_url}/bot{settings.bot_token}/{method}",
        data=json.dumps(payload or {}).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = json.loads(response.read())
    except urllib.error.HTTPError as error:
        description = error.read().decode(errors="replace")[:200]
        if error.code in PERMANENT_CODES:
            raise TelegramPermanentError(f"{error.code}: {description}") from error
        raise TelegramTemporaryError(f"{error.code}: {description}") from error
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise TelegramTemporaryError(str(error) or type(error).__name__) from error
    if not body.get("ok"):
        raise TelegramTemporaryError(str(body)[:200])
    return body.get("result")


def send_message(chat_id: int, text: str, reply_markup: dict | None = None) -> None:
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup
    call("sendMessage", payload)
