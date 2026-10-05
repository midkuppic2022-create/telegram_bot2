import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.redis import RedisStorage
from aiogram.types import ErrorEvent

from app.bot_commands import commands_for_role, configure_user_commands
from app.config import get_settings
from app.db import create_engine, create_session_factory
from app.middleware import DatabaseContextMiddleware
from app.routers import build_root_router
from app.seed import seed_reference_data
from app.services.users import UserService


async def configure_commands(bot: Bot) -> None:
    await bot.set_my_commands(commands_for_role(None))
    await bot.set_my_short_description("Учёт инспекций и отчёты для сотрудников.")
    await bot.set_my_description(
        "Учёт инспекций: добавление заказов, статистика и личные Excel-файлы "
        "для сотрудников; общая выгрузка для администраторов."
    )


async def main() -> None:
    settings = get_settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stdout,
    )
    logger = logging.getLogger("inspection_bot")

    engine = create_engine(settings.database_url)
    session_factory = create_session_factory(engine)
    async with session_factory() as session:
        await seed_reference_data(session)
        await UserService(session).bootstrap_admins(settings.admin_telegram_ids)
        await session.commit()

    storage = RedisStorage.from_url(settings.redis_url)
    bot = Bot(
        token=settings.bot_token.get_secret_value(),
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dispatcher = Dispatcher(storage=storage)
    dispatcher["settings"] = settings
    middleware = DatabaseContextMiddleware(session_factory)
    dispatcher.message.outer_middleware(middleware)
    dispatcher.callback_query.outer_middleware(middleware)
    dispatcher.include_router(build_root_router())

    @dispatcher.errors()
    async def handle_error(event: ErrorEvent) -> bool:
        logger.exception("Unhandled update error", exc_info=event.exception)
        update = event.update
        message = update.message or (
            update.callback_query.message if update.callback_query else None
        )
        if message is not None:
            try:
                await message.answer(
                    "⚠️ <b>Что-то пошло не так</b>\n\n"
                    "Попробуйте ещё раз. Если вы были в процессе ввода, нажмите /cancel и начните заново."
                )
            except Exception:
                pass
        return True

    try:
        await configure_commands(bot)
        async with session_factory() as session:
            for user in await UserService(session).list_for_reports():
                try:
                    await configure_user_commands(
                        bot, chat_id=user.telegram_id, role=user.role
                    )
                except Exception:
                    logger.warning(
                        "Could not refresh Telegram commands for user %s",
                        user.telegram_id,
                    )
        logger.info("Starting bot in long-polling mode")
        await dispatcher.start_polling(bot, allowed_updates=dispatcher.resolve_used_update_types())
    finally:
        await storage.close()
        await bot.session.close()
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
