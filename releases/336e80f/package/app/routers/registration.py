import hmac

from aiogram import Bot, F, Router, html
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot_commands import configure_user_commands
from app.config import Settings
from app.enums import UserRole, UserStatus
from app.errors import DomainError
from app.keyboards import (
    RegistrationRoleCallback,
    main_menu,
    registration_decision_keyboard,
    registration_role_keyboard,
)
from app.models import User
from app.presentation import role_label
from app.services.users import UserService
from app.states import RegistrationStates
from app.validators import validate_full_name

router = Router(name="registration")


async def _begin_registration(
    message: Message, state: FSMContext, settings: Settings
) -> None:
    if settings.registration_invite_code is not None:
        await state.set_state(RegistrationStates.invite_code)
        await message.answer(
            "🔐 <b>Код доступа</b>\n\n"
            "Введите код, который вы получили у администратора. После этого можно будет отправить заявку."
        )
        return
    await state.set_state(RegistrationStates.full_name)
    await message.answer(
        "👋 <b>Добро пожаловать!</b>\n\n"
        "Я помогу вести инспекции, считать результаты и собирать Excel-отчёты.\n\n"
        "<b>Шаг 1 из 2 · Представьтесь</b>\n"
        "Напишите ФИО или понятное рабочее имя.\n"
        "Например: <code>Иванов Иван</code>"
    )


@router.message(CommandStart())
async def start(
    message: Message,
    state: FSMContext,
    current_user: User | None,
    session: AsyncSession,
    bot: Bot,
    settings: Settings,
) -> None:
    await state.clear()
    if current_user is None:
        await configure_user_commands(bot, chat_id=message.chat.id, role=None)
        await _begin_registration(message, state, settings)
        return
    current_user = (
        await UserService(session).sync_telegram_profile(
            telegram_id=message.from_user.id,
            username=message.from_user.username,
            telegram_name=message.from_user.full_name,
        )
        or current_user
    )
    await session.commit()
    await configure_user_commands(bot, chat_id=message.chat.id, role=current_user.role)
    if current_user.status == UserStatus.PENDING:
        await message.answer(
            "⏳ <b>Заявка на рассмотрении</b>\n\n"
            "Администратор уже получил её. Я пришлю сообщение сразу после решения."
        )
        return
    if current_user.status == UserStatus.REJECTED:
        await message.answer(
            "⚪️ <b>Предыдущая заявка отклонена</b>\n\n"
            "Можно подать новую заявку."
        )
        await _begin_registration(message, state, settings)
        return
    if current_user.status == UserStatus.BLOCKED:
        await message.answer(
            "🔒 <b>Доступ приостановлен</b>\n\n"
            "Профиль заблокирован. Для восстановления обратитесь к администратору."
        )
        return
    await message.answer(
        "👋 <b>С возвращением, "
        f"{html.quote(current_user.full_name)}!</b>\n"
        f"Ваша роль: {role_label(current_user.role)}\n\n"
        "Выберите действие в меню ниже.",
        reply_markup=main_menu(current_user),
    )


@router.message(RegistrationStates.invite_code, F.text)
async def registration_invite_code(
    message: Message, state: FSMContext, settings: Settings
) -> None:
    configured = settings.registration_invite_code
    if configured is None:
        await _begin_registration(message, state, settings)
        return
    supplied = message.text.strip().casefold()
    expected = configured.get_secret_value().strip().casefold()
    if not hmac.compare_digest(supplied, expected):
        await message.answer(
            "⛔️ <b>Код не подошёл</b>\n\n"
            "Проверьте написание или запросите актуальный код у администратора."
        )
        return
    await state.set_state(RegistrationStates.full_name)
    await message.answer(
        "✅ <b>Код принят</b>\n\n"
        "<b>Шаг 1 из 2 · Представьтесь</b>\n"
        "Напишите ФИО или понятное рабочее имя.\n"
        "Например: <code>Иванов Иван</code>"
    )


@router.message(RegistrationStates.full_name, F.text)
async def registration_name(message: Message, state: FSMContext) -> None:
    try:
        full_name = validate_full_name(message.text)
    except DomainError as exc:
        await message.answer(f"⚠️ <b>Не получилось сохранить имя</b>\n\n{html.quote(str(exc))}")
        return
    await state.update_data(full_name=full_name)
    await state.set_state(RegistrationStates.role)
    await message.answer(
        "👤 <b>Шаг 2 из 2 · Выберите роль</b>\n\n"
        "Кем вы работаете? Администратор проверит заявку и подтвердит итоговую роль.",
        reply_markup=registration_role_keyboard(),
    )


@router.callback_query(RegistrationRoleCallback.filter(), RegistrationStates.role)
async def registration_role(
    callback: CallbackQuery,
    callback_data: RegistrationRoleCallback,
    state: FSMContext,
    session: AsyncSession,
    bot: Bot,
    settings: Settings,
) -> None:
    if callback_data.role == "back":
        await state.set_state(RegistrationStates.full_name)
        await callback.answer()
        await callback.message.answer(
            "👤 <b>Измените имя</b>\n\nНапишите ФИО или понятное рабочее имя."
        )
        return
    if callback_data.role == "cancel":
        await state.clear()
        await callback.answer()
        await callback.message.answer(
            "✖️ <b>Регистрация отменена</b>\n\nВернуться к ней можно в любой момент: /start"
        )
        return
    role = UserRole(callback_data.role)
    if role not in {UserRole.EXPERT, UserRole.SPECIALIST}:
        await callback.answer("Недоступная роль", show_alert=True)
        return
    data = await state.get_data()
    user = await UserService(session).register_pending(
        telegram_id=callback.from_user.id,
        username=callback.from_user.username,
        full_name=data["full_name"],
        requested_role=role,
    )
    await session.commit()
    await state.clear()
    await callback.answer()
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer(
        "✅ <b>Заявка отправлена</b>\n\n"
        "Администратор получил уведомление. Я сообщу вам о его решении."
    )

    requested_role = "🦺 Эксперт" if role is UserRole.EXPERT else "📋 Специалист"
    notification = (
        "🆕 <b>Новая заявка на доступ</b>\n\n"
        f"👤 {html.quote(user.full_name)}\n"
        f"🪪 Telegram ID: <code>{user.telegram_id}</code>\n"
        f"🎯 Запрошенная роль: {requested_role}\n\n"
        "Назначьте итоговую роль или отклоните заявку."
    )
    for admin_id in settings.admin_telegram_ids:
        try:
            await bot.send_message(
                admin_id,
                notification,
                reply_markup=registration_decision_keyboard(user.id),
            )
        except Exception:
            continue
