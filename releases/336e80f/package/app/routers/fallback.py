from aiogram import Router
from aiogram.types import CallbackQuery, Message

from app.keyboards import main_menu
from app.models import User

router = Router(name="fallback")


@router.callback_query()
async def stale_callback(callback: CallbackQuery) -> None:
    await callback.answer("Эта кнопка уже неактуальна. Откройте раздел заново.", show_alert=True)


@router.message()
async def unknown_message(message: Message, current_user: User | None) -> None:
    await message.answer(
        "🤔 <b>Не понял это сообщение</b>\n\nВыберите действие в меню или откройте /help.",
        reply_markup=main_menu(current_user),
    )
