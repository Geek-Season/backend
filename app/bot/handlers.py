"""Что бот делает в ответ на сообщения и кнопки.

/start <ключ>  — пришёл по QR-коду с сайта: спрашиваем «Войти?» [Да] [Нет]
/start         — просто открыл бота: объясняем, что он умеет
/login         — выдаём 6-значный код для ввода на сайте

Функции из app.modules.auth.service синхронные (работают с базой и Redis
по очереди), а aiogram асинхронный. Поэтому вызываем их через
asyncio.to_thread — в отдельном потоке, чтобы бот не «замирал».
"""

import asyncio

from aiogram import F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiogram.types import User as TgUser

from app.core.config import settings
from app.core.db import SessionLocal
from app.core.redis import redis_client
from app.modules.auth import service

router = Router()

MINUTES = settings.login_ttl_seconds // 60


def _profile(tg_user: TgUser) -> service.TelegramProfile:
    return service.TelegramProfile(
        telegram_id=tg_user.id,
        first_name=tg_user.first_name,
        last_name=tg_user.last_name,
        username=tg_user.username,
    )


# ---------- Обёртки: сервис в отдельном потоке со своей сессией базы ----------

def _confirm(token: str, profile: service.TelegramProfile) -> bool:
    with SessionLocal() as db:
        return service.confirm_qr_login(redis_client, db, token, profile)


def _issue_code(profile: service.TelegramProfile) -> str:
    with SessionLocal() as db:
        return service.issue_code(redis_client, db, profile)


# ---------- Обработчики ----------

@router.message(CommandStart(deep_link=True))
async def start_with_token(message: Message, command: CommandObject):
    token = command.args
    pending = await asyncio.to_thread(service.is_qr_pending, redis_client, token)
    if not pending:
        await message.answer(
            "Ссылка для входа устарела или уже использована.\n"
            "Обновите страницу входа на сайте и отсканируйте новый QR-код."
        )
        return

    keyboard = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Да, войти", callback_data=f"qr:yes:{token}"),
        InlineKeyboardButton(text="✖ Нет", callback_data=f"qr:no:{token}"),
    ]])
    await message.answer(
        "Войти на сайт <b>Geek Season</b> с этим аккаунтом Telegram?\n\n"
        "Нажимайте «Да», только если вы <b>сами</b> только что открыли страницу входа. "
        "Если QR-код вам прислал кто-то другой — нажмите «Нет».",
        reply_markup=keyboard,
    )


@router.message(CommandStart())
async def start_plain(message: Message):
    await message.answer(
        f"Привет, {message.from_user.first_name}! Я бот сайта <b>Geek Season</b>.\n\n"
        "Как войти на сайт:\n"
        "• отсканируйте QR-код на странице входа, или\n"
        "• отправьте мне /login и введите код на сайте."
    )


@router.message(Command("login"))
async def login_code(message: Message):
    code = await asyncio.to_thread(_issue_code, _profile(message.from_user))
    await message.answer(
        f"Ваш код для входа: <code>{code}</code>\n"
        f"Введите его на сайте в течение {MINUTES} минут. Никому его не сообщайте."
    )


@router.callback_query(F.data.startswith("qr:"))
async def qr_answer(callback: CallbackQuery):
    _, answer, token = callback.data.split(":", 2)

    if answer == "yes":
        ok = await asyncio.to_thread(_confirm, token, _profile(callback.from_user))
        text = (
            "Готово ✅ Вернитесь на сайт — вход выполнится автоматически."
            if ok else "Ссылка устарела. Обновите страницу входа на сайте."
        )
    else:
        await asyncio.to_thread(service.decline_qr_login, redis_client, token)
        text = "Вход отменён."

    await callback.message.edit_text(text)  # убираем кнопки, чтобы не нажали второй раз
    await callback.answer()
