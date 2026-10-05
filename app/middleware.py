from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.services.users import UserService


class DatabaseContextMiddleware(BaseMiddleware):
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self.session_factory = session_factory

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        async with self.session_factory() as session:
            data["session"] = session
            telegram_user = data.get("event_from_user")
            data["current_user"] = (
                await UserService(session).get_by_telegram_id(telegram_user.id)
                if telegram_user is not None
                else None
            )
            try:
                return await handler(event, data)
            except Exception:
                await session.rollback()
                raise
