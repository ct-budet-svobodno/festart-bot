"""Запуск бота: python -m app.bot.main"""

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand

from app.bot.handlers import get_router
from app.bot.middlewares import ContextMiddleware
from app.config import settings
from app.db import init_db

logger = logging.getLogger(__name__)


async def set_commands(bot: Bot) -> None:
    await bot.set_my_commands(
        [
            BotCommand(command="start", description="Начать"),
            BotCommand(command="menu", description="Главное меню"),
            BotCommand(command="id", description="Мой Telegram ID"),
        ]
    )


async def run() -> None:
    if not settings.bot_token:
        raise SystemExit(
            "BOT_TOKEN не задан.\n"
            "Скопируй .env.example в .env и впиши токен от @BotFather."
        )

    await init_db()

    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dispatcher = Dispatcher(storage=MemoryStorage())
    dispatcher.update.outer_middleware(ContextMiddleware())
    dispatcher.include_router(get_router())

    me = await bot.get_me()
    logger.info("Бот запущен: @%s", me.username)
    # Регистр не важен: для Telegram Festart_bot и festart_bot — один и тот же
    # бот, и ложное предупреждение только сбивало бы с толку.
    if me.username and me.username.lower() != settings.bot_username.lower():
        logger.warning(
            "BOT_USERNAME в .env (%s) не совпадает с реальным (%s) — "
            "ссылки в QR-кодах будут вести не туда!",
            settings.bot_username,
            me.username,
        )

    await set_commands(bot)
    await bot.delete_webhook(drop_pending_updates=True)
    try:
        await dispatcher.start_polling(bot)
    finally:
        await bot.session.close()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )
    try:
        asyncio.run(run())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Остановлено")


if __name__ == "__main__":
    main()
