from aiogram import Bot
from aiogram.types import BotCommand, BotCommandScopeChat

from app.enums import UserRole


def commands_for_role(role: UserRole | None) -> list[BotCommand]:
    commands = [BotCommand(command="start", description="🏠 Главное меню")]
    if role in {UserRole.EXPERT, UserRole.SPECIALIST, UserRole.ADMIN}:
        commands.extend(
            [
                BotCommand(command="add", description="➕ Новая инспекция"),
                BotCommand(command="my", description="📊 Мой месяц"),
                BotCommand(command="export", description="📤 Выгрузить Excel"),
            ]
        )
    if role in {UserRole.SPECIALIST, UserRole.ADMIN}:
        commands.append(BotCommand(command="report", description="📈 Общий отчёт"))
    if role is UserRole.ADMIN:
        commands.extend(
            [
                BotCommand(command="admin", description="⚙️ Управление"),
            ]
        )
    commands.extend(
        [
            BotCommand(command="cancel", description="✖️ Отменить действие"),
            BotCommand(command="help", description="ℹ️ Помощь"),
        ]
    )
    return commands


async def configure_user_commands(bot: Bot, *, chat_id: int, role: UserRole | None) -> None:
    await bot.set_my_commands(commands_for_role(role), scope=BotCommandScopeChat(chat_id=chat_id))
