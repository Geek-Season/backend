"""Запуск бота:  python -m app.bot

Бот сам раз в несколько секунд спрашивает у Telegram новые сообщения
(long polling), поэтому ему не нужен ни домен, ни публичный адрес —
работает прямо с твоего компьютера.
"""

import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from app.bot.handlers import router
from app.core.config import settings


async def main() -> None:
    if not settings.bot_token:
        # Код выхода 0, чтобы Docker не перезапускал бота по кругу без токена.
        print("Не задан BOT_TOKEN в файле .env — получите токен у @BotFather. Бот не запущен.")
        sys.exit(0)

    bot = Bot(settings.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dispatcher = Dispatcher()
    dispatcher.include_router(router)

    me = await bot.get_me()
    if settings.bot_username and settings.bot_username.lower() != (me.username or "").lower():
        logging.warning(
            "BOT_USERNAME=%s в .env не совпадает с настоящим именем бота @%s — "
            "QR-ссылки на сайте будут вести не туда.",
            settings.bot_username, me.username,
        )
    logging.info("Бот @%s запущен", me.username)
    await dispatcher.start_polling(bot)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
