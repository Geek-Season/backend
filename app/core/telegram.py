"""Отправка сообщения в Telegram через Bot API (обычный HTTPS-запрос).

Ошибки делятся на два вида:
- TelegramTemporaryError — сеть, 429 «слишком часто», 5xx: стоит повторить позже;
- TelegramPermanentError — 400/403 (бот заблокирован, чат не найден): повтор не поможет.
"""

import json
import urllib.error
import urllib.request

from app.core.config import settings


class TelegramTemporaryError(Exception):
    pass


class TelegramPermanentError(Exception):
    pass


def send_message(chat_id: int, text: str) -> None:
    if not settings.bot_token:
        raise TelegramPermanentError("BOT_TOKEN не задан")
    request = urllib.request.Request(
        f"{settings.telegram_api_url}/bot{settings.bot_token}/sendMessage",
        data=json.dumps({"chat_id": chat_id, "text": text, "parse_mode": "HTML"}).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            body = json.loads(response.read())
    except urllib.error.HTTPError as error:
        description = error.read().decode(errors="replace")[:200]
        if error.code in (400, 403):
            raise TelegramPermanentError(f"{error.code}: {description}") from error
        raise TelegramTemporaryError(f"{error.code}: {description}") from error
    except (urllib.error.URLError, TimeoutError) as error:
        raise TelegramTemporaryError(str(error)) from error
    if not body.get("ok"):
        raise TelegramTemporaryError(str(body)[:200])
