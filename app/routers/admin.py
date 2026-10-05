from io import BytesIO

from aiogram import Bot, F, Router, html
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot_commands import configure_user_commands
from app.enums import ExportGroup, SupplierStatus, UserRole, UserStatus
from app.errors import DomainError
from app.keyboards import (
    BTN_ADMIN,
    MAIN_MENU_BUTTONS,
    AdminCallback,
    AdminPageCallback,
    DirectoryCallback,
    RegistrationDecisionCallback,
    UserManageCallback,
    admin_home_keyboard,
    admin_list_pagination_keyboard,
    admin_menu_keyboard,
    directory_actions_keyboard,
    directory_list_keyboard,
    merge_confirmation_keyboard,
    merge_targets_keyboard,
    pending_supplier_keyboard,
    project_group_keyboard,
    registration_decision_keyboard,
    reject_supplier_confirmation_keyboard,
    status_duplicate_keyboard,
    user_actions_keyboard,
    user_block_confirm_keyboard,
    user_management_keyboard,
    user_role_confirm_keyboard,
)
from app.models import User
from app.presentation import (
    project_icon,
    role_label,
    supplier_status_label,
    user_status_label,
)
from app.routers.utils import require_active
from app.services.directories import DirectoryKind, DirectoryService
from app.services.reconciliation import MAX_REFERENCE_FILE_BYTES, ReconciliationService
from app.services.users import UserService
from app.states import DirectoryEditStates, ReferenceUploadStates

router = Router(name="admin")
ADMIN_PAGE_SIZE = 20
PENDING_PAGE_SIZE = 10


def _kind_label(kind: str) -> str:
    return {
        "status": "📌 Статусы",
        "project": "🧭 Проекты",
        "supplier": "🏭 Поставщики",
    }[kind]


async def _show_admin_menu(event: Message | CallbackQuery, session: AsyncSession) -> None:
    pending_users = len(await UserService(session).list_pending())
    message = event.message if isinstance(event, CallbackQuery) else event
    await message.answer(
        "⚙️ <b>Центр управления</b>\n\n"
        f"👥 Заявок на доступ: <b>{pending_users}</b>\n"
        "🏭 Поставщики теперь определяются по загруженному файлу.\n\n"
        "Выберите раздел:",
        reply_markup=admin_menu_keyboard(pending_users=pending_users),
    )


async def _show_directory(
    event: Message | CallbackQuery,
    session: AsyncSession,
    kind: DirectoryKind,
    *,
    page: int = 0,
) -> None:
    service = DirectoryService(session)
    if kind == "status":
        entities = await service.list_statuses(active_only=False)
    elif kind == "project":
        entities = await service.list_projects(active_only=False)
    else:
        entities = await service.list_suppliers(active_only=False)
    message = event.message if isinstance(event, CallbackQuery) else event
    await message.answer(
        f"<b>{_kind_label(kind)}</b>\n\n"
        "Зелёные значения доступны при вводе. Неактивные сохраняются в истории, "
        "но больше не предлагаются пользователям.",
        reply_markup=directory_list_keyboard(
            kind,
            entities,
            page=page,
            allow_add=False,
        ),
    )


async def _show_pending_users(
    event: Message | CallbackQuery,
    session: AsyncSession,
    *,
    page: int,
) -> None:
    results = await UserService(session).list_pending(
        limit=PENDING_PAGE_SIZE + 1,
        offset=page * PENDING_PAGE_SIZE,
    )
    users = results[:PENDING_PAGE_SIZE]
    message = event.message if isinstance(event, CallbackQuery) else event
    if not users:
        await message.answer(
            "✅ <b>Новых заявок нет</b>\n\nВсе обращения обработаны.",
            reply_markup=admin_list_pagination_keyboard("pending_users", page=page, has_next=False),
        )
        return
    await message.answer(f"👥 <b>Заявки на доступ</b> · страница {page + 1}")
    service = UserService(session)
    for user in users:
        requested_role = role_label(user.requested_role)
        sent = await message.answer(
            f"👤 <b>{html.quote(user.full_name)}</b>\n"
            f"🪪 Telegram ID: <code>{user.telegram_id}</code>\n"
            f"🎯 Запрошено: {requested_role}",
            reply_markup=registration_decision_keyboard(user.id),
        )
        message_id = getattr(sent, "message_id", None)
        chat_id = getattr(getattr(sent, "chat", None), "id", None)
        if isinstance(chat_id, int) and isinstance(message_id, int):
            await service.record_registration_notification(
                user_id=user.id,
                admin_chat_id=chat_id,
                telegram_message_id=message_id,
            )
    await session.commit()
    navigation = admin_list_pagination_keyboard(
        "pending_users",
        page=page,
        has_next=len(results) > PENDING_PAGE_SIZE,
    )
    await message.answer("Навигация:", reply_markup=navigation)


def _registration_resolution_text(
    user: User, *, processed_by: User | None = None
) -> str:
    if user.status is UserStatus.REJECTED:
        result = "❌ Заявка отклонена"
    else:
        result = f"✅ Итоговая роль: {role_label(user.role)}"
    processed_line = (
        f"\n👤 Обработал: {html.quote(processed_by.full_name)}"
        if processed_by is not None
        else "\n👤 Решение уже принято другим администратором"
    )
    return (
        "✅ <b>Заявка обработана</b>\n\n"
        f"👤 {html.quote(user.full_name)}\n"
        f"🪪 Telegram ID: <code>{user.telegram_id}</code>\n"
        f"{result}{processed_line}"
    )


async def _sync_registration_notifications(
    bot: Bot,
    service: UserService,
    user: User,
    *,
    text: str,
    current_message: Message | None = None,
) -> None:
    targets = {
        (item.admin_chat_id, item.telegram_message_id)
        for item in await service.list_registration_notifications(user.id)
    }
    if current_message is not None:
        chat_id = getattr(getattr(current_message, "chat", None), "id", None)
        message_id = getattr(current_message, "message_id", None)
        if isinstance(chat_id, int) and isinstance(message_id, int):
            targets.add((chat_id, message_id))
    for chat_id, message_id in targets:
        try:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=text,
                reply_markup=None,
            )
        except Exception:
            continue


async def _show_managed_users(
    event: Message | CallbackQuery,
    session: AsyncSession,
    *,
    page: int,
) -> None:
    results = await UserService(session).list_managed(
        limit=ADMIN_PAGE_SIZE + 1,
        offset=page * ADMIN_PAGE_SIZE,
    )
    users = results[:ADMIN_PAGE_SIZE]
    message = event.message if isinstance(event, CallbackQuery) else event
    await message.answer(
        f"👤 <b>Пользователи</b>\n\nСтраница {page + 1}. Выберите профиль для просмотра:",
        reply_markup=user_management_keyboard(
            users,
            page=page,
            has_next=len(results) > ADMIN_PAGE_SIZE,
        ),
    )


async def _show_pending_suppliers(
    event: Message | CallbackQuery,
    session: AsyncSession,
    *,
    page: int,
) -> None:
    results = await DirectoryService(session).list_suppliers(
        active_only=False,
        status=SupplierStatus.PENDING,
        limit=PENDING_PAGE_SIZE + 1,
        offset=page * PENDING_PAGE_SIZE,
    )
    suppliers = results[:PENDING_PAGE_SIZE]
    message = event.message if isinstance(event, CallbackQuery) else event
    if not suppliers:
        await message.answer(
            "✅ <b>Поставщиков на проверке нет</b>\n\nСправочник актуален.",
            reply_markup=admin_list_pagination_keyboard(
                "pending_suppliers", page=page, has_next=False
            ),
        )
        return
    await message.answer(f"🆕 <b>Поставщики на проверке</b> · страница {page + 1}")
    for supplier in suppliers:
        await message.answer(
            f"🏭 <b>{html.quote(supplier.name)}</b>\n"
            "Добавлен экспертом и уже используется в инспекции.",
            reply_markup=pending_supplier_keyboard(supplier.id),
        )
    navigation = admin_list_pagination_keyboard(
        "pending_suppliers",
        page=page,
        has_next=len(results) > PENDING_PAGE_SIZE,
    )
    await message.answer("Навигация:", reply_markup=navigation)


@router.message(Command("admin"))
@router.message(F.text == BTN_ADMIN)
async def admin_start(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    current_user: User | None,
    bot: Bot,
) -> None:
    if not await require_active(message, current_user, {UserRole.ADMIN}):
        return
    await state.clear()
    await _show_admin_menu(message, session)


@router.callback_query(RegistrationDecisionCallback.filter())
async def registration_decision(
    callback: CallbackQuery,
    callback_data: RegistrationDecisionCallback,
    session: AsyncSession,
    current_user: User | None,
    bot: Bot,
) -> None:
    if not await require_active(callback, current_user, {UserRole.ADMIN}):
        return
    service = UserService(session)
    try:
        if callback_data.action == "reject":
            user = await service.reject(callback_data.user_id)
            decision_text = "Заявка отклонена"
            user_text = (
                "⚪️ <b>Заявка отклонена</b>\n\n"
                "Если данные изменились, подайте новую заявку через /start."
            )
        else:
            role = UserRole(callback_data.action)
            user = await service.approve(callback_data.user_id, role)
            assigned_role = role_label(role)
            decision_text = f"Назначен: {role.value}"
            user_text = (
                "✅ <b>Доступ открыт</b>\n\n"
                f"Ваша роль: {assigned_role}\n"
                "Откройте главное меню: /start"
            )
    except (ValueError, DomainError) as exc:
        await session.rollback()
        try:
            user = await service.get(callback_data.user_id)
        except DomainError:
            await callback.answer(str(exc), show_alert=True)
            return
        if user.status is not UserStatus.PENDING:
            await _sync_registration_notifications(
                bot,
                service,
                user,
                text=_registration_resolution_text(user),
                current_message=callback.message,
            )
            await callback.answer("Заявка уже обработана. Уведомление обновлено.")
            return
        await callback.answer(str(exc), show_alert=True)
        return
    await session.commit()
    await _sync_registration_notifications(
        bot,
        service,
        user,
        text=_registration_resolution_text(user, processed_by=current_user),
        current_message=callback.message,
    )
    try:
        await configure_user_commands(bot, chat_id=user.telegram_id, role=user.role)
    except Exception:
        pass
    await callback.answer(decision_text)
    try:
        await bot.send_message(user.telegram_id, user_text)
    except Exception:
        pass


@router.callback_query(AdminCallback.filter())
async def admin_section(
    callback: CallbackQuery,
    callback_data: AdminCallback,
    session: AsyncSession,
    current_user: User | None,
    state: FSMContext,
) -> None:
    if not await require_active(callback, current_user, {UserRole.ADMIN}):
        return
    await callback.answer()
    section = callback_data.section
    await state.clear()
    if section == "home":
        await _show_admin_menu(callback, session)
        return
    if section in {"status", "project"}:
        await _show_directory(callback, session, section)
        return
    if section == "pending_users":
        await _show_pending_users(callback, session, page=0)
        return
    if section == "users":
        await _show_managed_users(callback, session, page=0)
        return
    if section == "reference_upload":
        active = await ReconciliationService(session).get_active_upload()
        await state.set_state(ReferenceUploadStates.file)
        current = (
            f"\n\nСейчас активен: <b>{html.quote(active.original_filename)}</b>\n"
            f"Заказов: <b>{active.row_count}</b> · версия #{active.id}"
            if active is not None
            else "\n\nАктивный файл пока не загружен."
        )
        await callback.message.answer(
            "📥 <b>Загрузка данных для сверки</b>\n\n"
            "Отправьте один файл <code>.xlsx</code> с колонками «Заказ» и «Поставщик». "
            "Новая версия включится только после полной успешной проверки."
            f"{current}",
            reply_markup=admin_home_keyboard(),
        )
        return
    if section in {"supplier", "pending_suppliers"}:
        await callback.message.answer(
            "ℹ️ Справочник поставщиков больше не используется. "
            "Поставщик автоматически берётся из файла сверки.",
            reply_markup=admin_home_keyboard(),
        )
        return
    await callback.message.answer(
        "⚠️ Раздел больше не доступен.", reply_markup=admin_home_keyboard()
    )


@router.message(ReferenceUploadStates.file, F.document)
async def reference_upload_file(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    current_user: User | None,
    bot: Bot,
) -> None:
    if not await require_active(message, current_user, {UserRole.ADMIN}):
        return
    document = message.document
    if document is None:
        return
    if document.file_size and document.file_size > MAX_REFERENCE_FILE_BYTES:
        await message.answer("⚠️ Файл слишком большой. Максимальный размер — 10 МБ.")
        return
    filename = document.file_name or "reference.xlsx"
    try:
        telegram_file = await bot.get_file(document.file_id)
        buffer = BytesIO()
        await bot.download_file(telegram_file.file_path, destination=buffer)
        upload = await ReconciliationService(session).import_xlsx(
            content=buffer.getvalue(),
            filename=filename,
            uploaded_by=current_user,
        )
        await session.commit()
    except DomainError as exc:
        await session.rollback()
        await message.answer(
            f"⚠️ <b>Файл не загружен</b>\n\n{html.quote(str(exc))}\n\n"
            "Предыдущая версия осталась активной.",
            reply_markup=admin_home_keyboard(),
        )
        return
    except SQLAlchemyError:
        await session.rollback()
        await message.answer(
            "⚠️ <b>Файл не загружен</b>\n\n"
            "Не удалось сохранить новую версию. Предыдущая версия осталась активной.",
            reply_markup=admin_home_keyboard(),
        )
        return
    await state.clear()
    warning = (
        f"\nСтрок без поставщика: <b>{upload.warning_count}</b>."
        if upload.warning_count
        else ""
    )
    await message.answer(
        "✅ <b>Данные для сверки обновлены</b>\n\n"
        f"Файл: <b>{html.quote(upload.original_filename)}</b>\n"
        f"Заказов: <b>{upload.row_count}</b>\n"
        f"Версия: <b>#{upload.id}</b>{warning}",
        reply_markup=admin_home_keyboard(),
    )


@router.message(ReferenceUploadStates.file)
async def reference_upload_wrong_message(message: Message) -> None:
    await message.answer("Отправьте файл в формате .xlsx или вернитесь в управление.")


@router.callback_query(DirectoryCallback.filter(F.kind == "supplier"))
async def obsolete_supplier_callback(callback: CallbackQuery) -> None:
    await callback.answer("Раздел поставщиков больше не используется", show_alert=True)


@router.callback_query(AdminPageCallback.filter())
async def admin_page(
    callback: CallbackQuery,
    callback_data: AdminPageCallback,
    session: AsyncSession,
    current_user: User | None,
) -> None:
    if not await require_active(callback, current_user, {UserRole.ADMIN}):
        return
    await callback.answer()
    page = max(callback_data.page, 0)
    if callback_data.section == "pending_users":
        await _show_pending_users(callback, session, page=page)
    elif callback_data.section == "users":
        await _show_managed_users(callback, session, page=page)
    elif callback_data.section == "pending_suppliers":
        await callback.message.answer(
            "ℹ️ Модерация поставщиков больше не используется. "
            "Поставщик автоматически берётся из файла сверки.",
            reply_markup=admin_home_keyboard(),
        )
    else:
        await callback.message.answer(
            "⚠️ Раздел больше не доступен.", reply_markup=admin_home_keyboard()
        )


@router.callback_query(DirectoryCallback.filter(F.action.startswith("page_")))
async def directory_page(
    callback: CallbackQuery,
    callback_data: DirectoryCallback,
    session: AsyncSession,
    current_user: User | None,
) -> None:
    if not await require_active(callback, current_user, {UserRole.ADMIN}):
        return
    await callback.answer()
    page = int(callback_data.action.removeprefix("page_"))
    await _show_directory(callback, session, callback_data.kind, page=page)


@router.callback_query(DirectoryCallback.filter(F.action == "view"))
async def directory_view(
    callback: CallbackQuery,
    callback_data: DirectoryCallback,
    session: AsyncSession,
    current_user: User | None,
) -> None:
    if not await require_active(callback, current_user, {UserRole.ADMIN}):
        return
    entity = await DirectoryService(session).get_entity(callback_data.kind, callback_data.entity_id)
    if entity is None:
        await callback.answer("Элемент не найден", show_alert=True)
        return
    if callback_data.kind == "supplier":
        active = entity.status == SupplierStatus.ACTIVE
        pending = entity.status == SupplierStatus.PENDING
        details = f"Состояние: {supplier_status_label(entity.status)}"
    elif callback_data.kind == "project":
        active = entity.active
        pending = False
        details = f"Лист выгрузки: {project_icon(entity.export_group)} {entity.export_group.value}"
    else:
        active = entity.active
        pending = False
        details = "Повторные номера: " + (
            "✅ разрешены" if entity.allows_duplicate else "🚫 запрещены"
        )
    await callback.answer()
    await callback.message.answer(
        f"<b>{_kind_label(callback_data.kind)}</b>\n\n{html.quote(entity.name)}\n{details}",
        reply_markup=directory_actions_keyboard(
            callback_data.kind, entity.id, active=active, pending=pending
        ),
    )


@router.callback_query(DirectoryCallback.filter(F.action == "add"))
async def directory_add_start(
    callback: CallbackQuery,
    callback_data: DirectoryCallback,
    state: FSMContext,
    current_user: User | None,
) -> None:
    if not await require_active(callback, current_user, {UserRole.ADMIN}):
        return
    if callback_data.kind in {"status", "project", "supplier"}:
        await callback.answer(
            "Набор сценариев и проектов зафиксирован в версии 2",
            show_alert=True,
        )
        return
    await state.set_state(DirectoryEditStates.name)
    await state.update_data(admin_action="add", directory_kind=callback_data.kind)
    await callback.answer()
    await callback.message.answer(
        f"➕ <b>Новое значение · {_kind_label(callback_data.kind)}</b>\n\nВведите название:",
        reply_markup=admin_home_keyboard(),
    )


@router.message(DirectoryEditStates.name, F.text, ~F.text.in_(MAIN_MENU_BUTTONS))
async def directory_add_name(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    current_user: User | None,
) -> None:
    if not await require_active(message, current_user, {UserRole.ADMIN}):
        return
    data = await state.get_data()
    kind = data["directory_kind"]
    await state.update_data(directory_name=message.text)
    if kind == "status":
        await state.set_state(DirectoryEditStates.status_duplicate)
        await message.answer(
            "🔁 <b>Правило повторных заказов</b>\n\n"
            "Можно ли с этим статусом сохранять уже существующий номер заказа?",
            reply_markup=status_duplicate_keyboard(),
        )
    elif kind == "project":
        await state.set_state(DirectoryEditStates.project_group)
        await message.answer(
            "🗂 <b>Группа выгрузки</b>\n\nНа какой лист Excel попадёт проект?",
            reply_markup=project_group_keyboard(),
        )
    else:
        try:
            await DirectoryService(session).add_supplier(
                message.text, active=True, created_by_id=current_user.id
            )
            await session.commit()
        except DomainError as exc:
            await session.rollback()
            await message.answer(f"⚠️ {html.quote(str(exc))}", reply_markup=admin_home_keyboard())
            return
        await state.clear()
        await message.answer("✅ <b>Поставщик добавлен</b>", reply_markup=admin_home_keyboard())


@router.callback_query(
    DirectoryCallback.filter(F.action.in_({"duplicate_yes", "duplicate_no"})),
    DirectoryEditStates.status_duplicate,
)
async def directory_status_finish(
    callback: CallbackQuery,
    callback_data: DirectoryCallback,
    state: FSMContext,
    session: AsyncSession,
    current_user: User | None,
) -> None:
    if not await require_active(callback, current_user, {UserRole.ADMIN}):
        return
    data = await state.get_data()
    try:
        await DirectoryService(session).add_status(
            data["directory_name"], allows_duplicate=callback_data.action == "duplicate_yes"
        )
        await session.commit()
    except DomainError as exc:
        await session.rollback()
        await callback.answer(str(exc), show_alert=True)
        return
    await state.clear()
    await callback.answer("Добавлено")
    await callback.message.answer("✅ <b>Статус добавлен</b>", reply_markup=admin_home_keyboard())


@router.callback_query(
    DirectoryCallback.filter(F.action.startswith("group_")),
    DirectoryEditStates.project_group,
)
async def directory_project_finish(
    callback: CallbackQuery,
    callback_data: DirectoryCallback,
    state: FSMContext,
    session: AsyncSession,
    current_user: User | None,
) -> None:
    if not await require_active(callback, current_user, {UserRole.ADMIN}):
        return
    group_name = callback_data.action.removeprefix("group_").upper()
    data = await state.get_data()
    try:
        await DirectoryService(session).add_project(
            data["directory_name"], export_group=ExportGroup[group_name]
        )
        await session.commit()
    except DomainError as exc:
        await session.rollback()
        await callback.answer(str(exc), show_alert=True)
        return
    await state.clear()
    await callback.answer("Добавлено")
    await callback.message.answer("✅ <b>Проект добавлен</b>", reply_markup=admin_home_keyboard())


@router.callback_query(DirectoryCallback.filter(F.action == "rename"))
async def directory_rename_start(
    callback: CallbackQuery,
    callback_data: DirectoryCallback,
    state: FSMContext,
    current_user: User | None,
) -> None:
    if not await require_active(callback, current_user, {UserRole.ADMIN}):
        return
    await state.set_state(DirectoryEditStates.rename)
    await state.update_data(
        directory_kind=callback_data.kind, directory_entity_id=callback_data.entity_id
    )
    await callback.answer()
    await callback.message.answer(
        "✏️ <b>Переименование</b>\n\nВведите новое название:",
        reply_markup=admin_home_keyboard(),
    )


@router.message(DirectoryEditStates.rename, F.text, ~F.text.in_(MAIN_MENU_BUTTONS))
async def directory_rename_finish(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    current_user: User | None,
) -> None:
    if not await require_active(message, current_user, {UserRole.ADMIN}):
        return
    data = await state.get_data()
    try:
        await DirectoryService(session).rename(
            data["directory_kind"], int(data["directory_entity_id"]), message.text
        )
        await session.commit()
    except DomainError as exc:
        await session.rollback()
        await message.answer(f"⚠️ {html.quote(str(exc))}", reply_markup=admin_home_keyboard())
        return
    await state.clear()
    await message.answer("✅ <b>Название обновлено</b>", reply_markup=admin_home_keyboard())


@router.callback_query(DirectoryCallback.filter(F.action.in_({"disable", "enable"})))
async def directory_toggle(
    callback: CallbackQuery,
    callback_data: DirectoryCallback,
    session: AsyncSession,
    current_user: User | None,
) -> None:
    if not await require_active(callback, current_user, {UserRole.ADMIN}):
        return
    await DirectoryService(session).set_active(
        callback_data.kind, callback_data.entity_id, callback_data.action == "enable"
    )
    await session.commit()
    await callback.answer("Сохранено")
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer(
        "✅ Состояние справочника обновлено.", reply_markup=admin_home_keyboard()
    )


@router.callback_query(DirectoryCallback.filter(F.action == "approve"))
async def supplier_approve(
    callback: CallbackQuery,
    callback_data: DirectoryCallback,
    session: AsyncSession,
    current_user: User | None,
) -> None:
    if not await require_active(callback, current_user, {UserRole.ADMIN}):
        return
    await DirectoryService(session).approve_supplier(callback_data.entity_id)
    await session.commit()
    await callback.answer("Поставщик подтверждён")
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer(
        "✅ <b>Поставщик подтверждён</b>", reply_markup=admin_home_keyboard()
    )


@router.callback_query(DirectoryCallback.filter(F.action == "reject"))
async def supplier_reject_confirm(
    callback: CallbackQuery,
    callback_data: DirectoryCallback,
    session: AsyncSession,
    current_user: User | None,
) -> None:
    if not await require_active(callback, current_user, {UserRole.ADMIN}):
        return
    supplier = await DirectoryService(session).get_supplier(callback_data.entity_id)
    if supplier is None:
        await callback.answer("Поставщик не найден", show_alert=True)
        return
    if supplier.status != SupplierStatus.PENDING:
        await callback.answer("Поставщик уже обработан", show_alert=True)
        return
    await callback.answer()
    await callback.message.answer(
        "⚠️ <b>Отклонить поставщика?</b>\n\n"
        f"{html.quote(supplier.name)}\n\n"
        "Он исчезнет из списка проверки и не будет предлагаться при новых инспекциях. "
        "В уже созданных инспекциях поставщик сохранится.",
        reply_markup=reject_supplier_confirmation_keyboard(supplier.id),
    )


@router.callback_query(DirectoryCallback.filter(F.action == "reject_do"))
async def supplier_reject_finish(
    callback: CallbackQuery,
    callback_data: DirectoryCallback,
    session: AsyncSession,
    current_user: User | None,
) -> None:
    if not await require_active(callback, current_user, {UserRole.ADMIN}):
        return
    try:
        await DirectoryService(session).reject_supplier(callback_data.entity_id)
        await session.commit()
    except DomainError as exc:
        await session.rollback()
        await callback.answer(str(exc), show_alert=True)
        return
    await callback.answer("Поставщик отклонён")
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer(
        "✅ <b>Поставщик отклонён</b>", reply_markup=admin_home_keyboard()
    )


@router.callback_query(DirectoryCallback.filter(F.action == "merge"))
async def supplier_merge_start(
    callback: CallbackQuery,
    callback_data: DirectoryCallback,
    state: FSMContext,
    current_user: User | None,
) -> None:
    if not await require_active(callback, current_user, {UserRole.ADMIN}):
        return
    await state.set_state(DirectoryEditStates.merge_query)
    await state.update_data(merge_source_id=callback_data.entity_id)
    await callback.answer()
    await callback.message.answer(
        "🔗 <b>Объединение поставщиков</b>\n\n"
        "Найдите правильное название. Все инспекции будут перенесены к нему.\n"
        "Введите часть названия:",
        reply_markup=admin_home_keyboard(),
    )


@router.message(DirectoryEditStates.merge_query, F.text, ~F.text.in_(MAIN_MENU_BUTTONS))
async def supplier_merge_search(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    current_user: User | None,
) -> None:
    if not await require_active(message, current_user, {UserRole.ADMIN}):
        return
    data = await state.get_data()
    suppliers = await DirectoryService(session).search_suppliers(message.text, limit=10)
    if not suppliers:
        await message.answer(
            "🔎 Совпадений нет. Попробуйте другой запрос.",
            reply_markup=admin_home_keyboard(),
        )
        return
    await message.answer(
        "🏭 <b>Выберите основного поставщика</b>\n\n"
        "После подтверждения все связанные инспекции будут перенесены к нему:",
        reply_markup=merge_targets_keyboard(int(data["merge_source_id"]), suppliers),
    )


@router.callback_query(DirectoryCallback.filter(F.action.startswith("merge_pick_")))
async def supplier_merge_confirm(
    callback: CallbackQuery,
    callback_data: DirectoryCallback,
    session: AsyncSession,
    current_user: User | None,
) -> None:
    if not await require_active(callback, current_user, {UserRole.ADMIN}):
        return
    source_id = int(callback_data.action.removeprefix("merge_pick_"))
    service = DirectoryService(session)
    source = await service.get_supplier(source_id)
    target = await service.get_supplier(callback_data.entity_id)
    if source is None or target is None:
        await callback.answer("Поставщик не найден", show_alert=True)
        return
    await callback.answer()
    await callback.message.answer(
        "⚠️ <b>Подтвердите объединение</b>\n\n"
        f"Из: <b>{html.quote(source.name)}</b>\n"
        f"В: <b>{html.quote(target.name)}</b>\n\n"
        "Все инспекции первого поставщика будут перенесены ко второму. Отменить это "
        "действие через бота нельзя.",
        reply_markup=merge_confirmation_keyboard(source_id, target.id),
    )


@router.callback_query(DirectoryCallback.filter(F.action.startswith("merge_do_")))
async def supplier_merge_finish(
    callback: CallbackQuery,
    callback_data: DirectoryCallback,
    state: FSMContext,
    session: AsyncSession,
    current_user: User | None,
) -> None:
    if not await require_active(callback, current_user, {UserRole.ADMIN}):
        return
    source_id = int(callback_data.action.removeprefix("merge_do_"))
    try:
        target = await DirectoryService(session).merge_supplier(source_id, callback_data.entity_id)
        await session.commit()
    except DomainError as exc:
        await session.rollback()
        await callback.answer(str(exc), show_alert=True)
        return
    await state.clear()
    await callback.answer("Объединено")
    await callback.message.answer(
        "✅ <b>Поставщики объединены</b>\n\n"
        f"Все записи теперь относятся к «{html.quote(target.name)}».",
        reply_markup=admin_home_keyboard(),
    )


@router.callback_query(UserManageCallback.filter())
async def user_manage(
    callback: CallbackQuery,
    callback_data: UserManageCallback,
    session: AsyncSession,
    current_user: User | None,
    bot: Bot,
) -> None:
    if not await require_active(callback, current_user, {UserRole.ADMIN}):
        return
    service = UserService(session)
    user = await service.get(callback_data.user_id)
    if callback_data.action == "view":
        await callback.answer()
        username = f"@{html.quote(user.username)}" if user.username else "не указан"
        await callback.message.answer(
            "👤 <b>Профиль пользователя</b>\n\n"
            f"Имя: <b>{html.quote(user.full_name)}</b>\n"
            f"Telegram: {username}\n"
            f"ID: <code>{user.telegram_id}</code>\n"
            f"Роль: {role_label(user.role)}\n"
            f"Состояние: {user_status_label(user.status)}",
            reply_markup=user_actions_keyboard(user),
        )
        return
    if callback_data.action == "block_confirm":
        await callback.answer()
        await callback.message.answer(
            "⚠️ <b>Заблокировать пользователя?</b>\n\n"
            f"{html.quote(user.full_name)} потеряет доступ ко всем функциям бота. "
            "Сохранённые инспекции останутся в базе.",
            reply_markup=user_block_confirm_keyboard(user.id),
        )
        return
    if callback_data.action.startswith("role_confirm_"):
        try:
            role = UserRole(callback_data.action.removeprefix("role_confirm_"))
        except ValueError:
            await callback.answer("Неизвестная роль", show_alert=True)
            return
        await callback.answer()
        await callback.message.answer(
            "⚠️ <b>Изменить роль пользователя?</b>\n\n"
            f"{html.quote(user.full_name)} станет: {role_label(role)}.\n"
            "Ранее сохранённые инспекции сохранят свой первоначальный вид работы.",
            reply_markup=user_role_confirm_keyboard(user.id, role),
        )
        return
    if callback_data.action.startswith("role_set_"):
        try:
            role = UserRole(callback_data.action.removeprefix("role_set_"))
            user = await service.change_role(callback_data.user_id, role)
            await session.commit()
        except (ValueError, DomainError) as exc:
            await session.rollback()
            await callback.answer(str(exc), show_alert=True)
            return
        try:
            await configure_user_commands(bot, chat_id=user.telegram_id, role=user.role)
            await bot.send_message(
                user.telegram_id,
                "🔄 <b>Ваша роль изменена</b>\n\n"
                f"Новая роль: {role_label(user.role)}\n"
                "Откройте главное меню: /start",
            )
        except Exception:
            pass
        await callback.answer("Роль изменена")
        await callback.message.answer(
            "✅ <b>Роль обновлена</b>\n\n"
            f"{html.quote(user.full_name)}: {role_label(user.role)}",
            reply_markup=user_actions_keyboard(user),
        )
        return
    try:
        user = await service.set_blocked(callback_data.user_id, callback_data.action == "block")
        await session.commit()
    except ValueError as exc:
        await session.rollback()
        await callback.answer(str(exc), show_alert=True)
        return
    await callback.answer("Состояние обновлено")
    await callback.message.answer(
        "✅ <b>Состояние пользователя обновлено</b>\n\n"
        f"{html.quote(user.full_name)}: {user_status_label(user.status)}",
        reply_markup=user_actions_keyboard(user),
    )
