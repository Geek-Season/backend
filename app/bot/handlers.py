"""Что бот делает в ответ на сообщения и кнопки.

/start <ключ>  — пришёл по QR-коду с сайта: спрашиваем «Войти?» [Да] [Нет]
/start         — просто открыл бота: объясняем, что он умеет
/login         — выдаём 6-значный код для ввода на сайте

handle_update() получает «обновление» от Telegram (словарь из getUpdates)
и отвечает через объект api — так обработчики легко проверять в тестах,
подставив вместо настоящего Telegram заглушку.
"""

from html import escape
from typing import Protocol

from redis import Redis
from sqlalchemy.orm import Session

from app.core import telegram
from app.core.config import settings
from app.modules.auth import service

MINUTES = settings.login_ttl_seconds // 60


class BotApi(Protocol):
    def send_message(self, chat_id: int, text: str, reply_markup: dict | None = None) -> None: ...
    def edit_message_text(self, chat_id: int, message_id: int, text: str) -> None: ...
    def answer_callback_query(self, callback_query_id: str) -> None: ...


class TelegramBotApi:
    """Настоящий Telegram (app/core/telegram.py)."""

    def send_message(self, chat_id: int, text: str, reply_markup: dict | None = None) -> None:
        telegram.send_message(chat_id, text, reply_markup)

    def edit_message_text(self, chat_id: int, message_id: int, text: str) -> None:
        telegram.call("editMessageText", {
            "chat_id": chat_id, "message_id": message_id, "text": text, "parse_mode": "HTML",
        })

    def answer_callback_query(self, callback_query_id: str) -> None:
        telegram.call("answerCallbackQuery", {"callback_query_id": callback_query_id})


def _profile(user: dict) -> service.TelegramProfile:
    return service.TelegramProfile(
        telegram_id=user["id"],
        first_name=user.get("first_name") or "",
        last_name=user.get("last_name"),
        username=user.get("username"),
    )


def _command(text: str) -> tuple[str, str]:
    """«/start@GeekSeasonBot abc» → («/start», «abc»)."""
    head, _, args = text.strip().partition(" ")
    return head.split("@", 1)[0].lower(), args.strip()


# ---------- Обработчики ----------

def start_with_token(api: BotApi, r: Redis, chat_id: int, token: str) -> None:
    if not service.is_qr_pending(r, token):
        api.send_message(
            chat_id,
            "Ссылка для входа устарела или уже использована.\n"
            "Обновите страницу входа на сайте и отсканируйте новый QR-код.",
        )
        return
    keyboard = {"inline_keyboard": [[
        {"text": "✅ Да, войти", "callback_data": f"qr:yes:{token}"},
        {"text": "✖ Нет", "callback_data": f"qr:no:{token}"},
    ]]}
    api.send_message(
        chat_id,
        "Войти на сайт <b>Geek Season</b> с этим аккаунтом Telegram?\n\n"
        "Нажимайте «Да», только если вы <b>сами</b> только что открыли страницу входа. "
        "Если QR-код вам прислал кто-то другой — нажмите «Нет».",
        reply_markup=keyboard,
    )


def start_plain(api: BotApi, chat_id: int, first_name: str) -> None:
    api.send_message(
        chat_id,
        f"Привет, {escape(first_name)}! Я бот сайта <b>Geek Season</b>.\n\n"
        "Как войти на сайт:\n"
        "• отсканируйте QR-код на странице входа, или\n"
        "• отправьте мне /login и введите код на сайте.",
    )


def login_code(api: BotApi, r: Redis, db: Session, chat_id: int, user: dict) -> None:
    code = service.issue_code(r, db, _profile(user))
    api.send_message(
        chat_id,
        f"Ваш код для входа: <code>{code}</code>\n"
        f"Введите его на сайте в течение {MINUTES} минут. Никому его не сообщайте.",
    )


def qr_answer(api: BotApi, r: Redis, db: Session, callback: dict) -> None:
    _, answer, token = callback["data"].split(":", 2)
    if answer == "yes":
        ok = service.confirm_qr_login(r, db, token, _profile(callback["from"]))
        text = (
            "Готово ✅ Вернитесь на сайт — вход выполнится автоматически."
            if ok else "Ссылка устарела. Обновите страницу входа на сайте."
        )
    else:
        service.decline_qr_login(r, token)
        text = "Вход отменён."

    message = callback.get("message")
    if message:  # убираем кнопки, чтобы не нажали второй раз
        api.edit_message_text(message["chat"]["id"], message["message_id"], text)
    api.answer_callback_query(callback["id"])


def handle_update(api: BotApi, r: Redis, db: Session, update: dict) -> None:
    """Разобрать одно обновление Telegram. Неизвестное — молча пропускаем."""
    callback = update.get("callback_query")
    if callback:
        if (callback.get("data") or "").startswith("qr:"):
            qr_answer(api, r, db, callback)
        else:
            api.answer_callback_query(callback["id"])
        return

    message = update.get("message")
    if not message or "text" not in message or "from" not in message:
        return
    chat_id = message["chat"]["id"]
    command, args = _command(message["text"])
    if command == "/start":
        if args:
            start_with_token(api, r, chat_id, args)
        else:
            start_plain(api, chat_id, message["from"].get("first_name") or "")
    elif command == "/login":
        login_code(api, r, db, chat_id, message["from"])
