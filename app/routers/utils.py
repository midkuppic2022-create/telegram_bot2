from aiogram.types import CallbackQuery, Message

from app.enums import UserRole, UserStatus
from app.keyboards import main_menu
from app.models import User


async def require_active(
    event: Message | CallbackQuery,
    user: User | None,
    roles: set[UserRole] | None = None,
) -> bool:
    message = event.message if isinstance(event, CallbackQuery) else event
    if user is None:
        await message.answer(
            "👋 <b>Сначала нужна регистрация</b>\n\nНажмите /start — это займёт меньше минуты."
        )
        if isinstance(event, CallbackQuery):
            await event.answer()
        return False
    if user.status != UserStatus.ACTIVE or user.role is None:
        status_text = (
            "Профиль заблокирован. Обратитесь к администратору."
            if user.status == UserStatus.BLOCKED
            else "Заявка ещё не активирована администратором."
        )
        await message.answer(f"🔒 <b>Доступ недоступен</b>\n\n{status_text}")
        if isinstance(event, CallbackQuery):
            await event.answer()
        return False
    if roles is not None and user.role not in roles:
        await message.answer(
            "🚫 <b>Функция недоступна для вашей роли</b>\n\n"
            "Выберите доступное действие в меню ниже.",
            reply_markup=main_menu(user),
        )
        if isinstance(event, CallbackQuery):
            await event.answer()
        return False
    return True


async def answer_event(event: Message | CallbackQuery, text: str, **kwargs) -> Message:
    if isinstance(event, CallbackQuery):
        await event.answer()
        return await event.message.answer(text, **kwargs)
    return await event.answer(text, **kwargs)
