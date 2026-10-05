from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from app.enums import UserRole, UserStatus
from app.keyboards import BTN_HELP, main_menu
from app.models import User
from app.presentation import role_label

router = Router(name="common")


@router.message(Command("help"))
@router.message(F.text == BTN_HELP)
async def help_command(message: Message, current_user: User | None) -> None:
    lines = [
        "ℹ️ <b>Помощь</b>",
        "",
        "Я сохраняю инспекции, считаю статистику и формирую Excel-отчёты.",
    ]
    if current_user is None:
        lines.extend(["", "Чтобы начать, зарегистрируйтесь: /start"])
    elif current_user.status != UserStatus.ACTIVE or current_user.role is None:
        lines.extend(["", "Ваш профиль пока не активен. Проверить состояние: /start"])
    else:
        lines.extend(["", f"Ваша роль: <b>{role_label(current_user.role)}</b>", ""])
        if current_user.role in {UserRole.EXPERT, UserRole.SPECIALIST, UserRole.ADMIN}:
            lines.extend(
                [
                    "➕ /add — новая инспекция",
                    "📊 /my — итоги текущего месяца",
                ]
            )
        if current_user.role in {UserRole.EXPERT, UserRole.SPECIALIST}:
            lines.append("📤 /export — мои инспекции в Excel")
        if current_user.role in {UserRole.SPECIALIST, UserRole.ADMIN}:
            lines.append("📈 /report — сводный отчёт")
        if current_user.role is UserRole.ADMIN:
            lines.extend(
                [
                    "📤 /export — Excel-выгрузка сотрудников",
                    "⚙️ /admin — пользователи и справочники",
                ]
            )
        lines.extend(["", "✖️ /cancel — отменить текущий ввод", "🏠 /start — главное меню"])
    await message.answer("\n".join(lines), reply_markup=main_menu(current_user))


@router.message(Command("cancel"))
async def cancel_command(message: Message, state: FSMContext, current_user: User | None) -> None:
    await state.clear()
    await message.answer(
        "✖️ <b>Действие отменено</b>\n\nДанные этого диалога не сохранены.",
        reply_markup=main_menu(current_user),
    )
