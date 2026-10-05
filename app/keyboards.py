from datetime import date

from aiogram.filters.callback_data import CallbackData
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.enums import ExportGroup, ExportMode, InspectionWorkType, UserRole, UserStatus
from app.models import ExportBatch, Inspection, InspectionStatus, Project, Supplier, User
from app.presentation import project_icon, status_icon

BTN_ADD = "➕ Новая инспекция"
BTN_MY = "📊 Мой месяц"
BTN_REPORT = "📈 Отчёты"
BTN_EXPORT = "📤 Excel"
BTN_ADMIN = "⚙️ Управление"
BTN_HELP = "ℹ️ Помощь"
MAIN_MENU_BUTTONS = frozenset({BTN_ADD, BTN_MY, BTN_REPORT, BTN_EXPORT, BTN_ADMIN, BTN_HELP})


class RegistrationRoleCallback(CallbackData, prefix="regrole"):
    role: str


class RegistrationDecisionCallback(CallbackData, prefix="regdec"):
    user_id: int
    action: str


class InspectionChoiceCallback(CallbackData, prefix="ich"):
    kind: str
    entity_id: int


class InspectionActionCallback(CallbackData, prefix="ia"):
    action: str


class BatchCallback(CallbackData, prefix="batch"):
    action: str
    inspection_date: str = "x"
    supplier_id: int = 0
    work_type: str = "x"


class PeriodCallback(CallbackData, prefix="period"):
    purpose: str
    choice: str


class ExpertFilterCallback(CallbackData, prefix="expertf"):
    purpose: str
    expert_id: int


class ExpertPageCallback(CallbackData, prefix="expage"):
    purpose: str
    page: int


class ExportModeCallback(CallbackData, prefix="exmode"):
    mode: str


class ExportRetryCallback(CallbackData, prefix="exretry"):
    batch_id: int


class ReportActionCallback(CallbackData, prefix="repact"):
    action: str
    purpose: str


class AdminCallback(CallbackData, prefix="adm"):
    section: str


class AdminPageCallback(CallbackData, prefix="admpage"):
    section: str
    page: int


class DirectoryCallback(CallbackData, prefix="dir"):
    kind: str
    action: str
    entity_id: int = 0


class UserManageCallback(CallbackData, prefix="usr"):
    action: str
    user_id: int


class MyMonthCallback(CallbackData, prefix="mymonth"):
    action: str
    inspection_id: int = 0
    page: int = 0


def main_menu(user: User | None) -> ReplyKeyboardMarkup | None:
    if user is None or user.status != UserStatus.ACTIVE or user.role is None:
        return None
    rows: list[list[KeyboardButton]] = []
    if user.role in {UserRole.EXPERT, UserRole.SPECIALIST, UserRole.ADMIN}:
        rows.append([KeyboardButton(text=BTN_ADD), KeyboardButton(text=BTN_MY)])
    if user.role is UserRole.EXPERT:
        rows.append([KeyboardButton(text=BTN_EXPORT)])
    if user.role is UserRole.SPECIALIST:
        rows.append([KeyboardButton(text=BTN_REPORT), KeyboardButton(text=BTN_EXPORT)])
    if user.role is UserRole.ADMIN:
        rows.append([KeyboardButton(text=BTN_REPORT), KeyboardButton(text=BTN_EXPORT)])
        rows.append([KeyboardButton(text=BTN_ADMIN)])
    rows.append([KeyboardButton(text=BTN_HELP)])
    return ReplyKeyboardMarkup(
        keyboard=rows,
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="Выберите действие",
    )


def registration_role_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text="🦺 Эксперт",
        callback_data=RegistrationRoleCallback(role=UserRole.EXPERT.value),
    )
    builder.button(
        text="📋 Специалист",
        callback_data=RegistrationRoleCallback(role=UserRole.SPECIALIST.value),
    )
    builder.button(text="⬅️ Изменить имя", callback_data=RegistrationRoleCallback(role="back"))
    builder.button(text="✖️ Отменить", callback_data=RegistrationRoleCallback(role="cancel"))
    builder.adjust(1)
    return builder.as_markup()


def registration_decision_keyboard(user_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text="✅ Назначить экспертом",
        callback_data=RegistrationDecisionCallback(user_id=user_id, action="expert"),
    )
    builder.button(
        text="📋 Назначить специалистом",
        callback_data=RegistrationDecisionCallback(user_id=user_id, action="specialist"),
    )
    builder.button(
        text="✖️ Отклонить",
        callback_data=RegistrationDecisionCallback(user_id=user_id, action="reject"),
    )
    builder.adjust(1)
    return builder.as_markup()


def work_type_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🦺 Работа эксперта",
                    callback_data=InspectionActionCallback(
                        action=f"work_{InspectionWorkType.EXPERT.value}"
                    ).pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="📋 Работа специалиста",
                    callback_data=InspectionActionCallback(
                        action=f"work_{InspectionWorkType.SPECIALIST.value}"
                    ).pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="✖️ Отменить",
                    callback_data=InspectionActionCallback(action="cancel").pack(),
                )
            ],
        ]
    )


def date_keyboard(*, include_back: bool = False) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text="📅 Сегодня",
                callback_data=InspectionActionCallback(action="date_today").pack(),
            ),
            InlineKeyboardButton(
                text="↩️ Вчера",
                callback_data=InspectionActionCallback(action="date_yesterday").pack(),
            ),
        ]
    ]
    if include_back:
        rows.append(
            [
                InlineKeyboardButton(
                    text="⬅️ К проверке",
                    callback_data=InspectionActionCallback(action="back_confirm").pack(),
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                text="✖️ Отменить", callback_data=InspectionActionCallback(action="cancel").pack()
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def navigation_keyboard(back_target: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⬅️ Назад",
                    callback_data=InspectionActionCallback(action=f"back_{back_target}").pack(),
                ),
                InlineKeyboardButton(
                    text="✖️ Отменить",
                    callback_data=InspectionActionCallback(action="cancel").pack(),
                ),
            ]
        ]
    )


def supplier_results_keyboard(
    suppliers: list[Supplier],
    *,
    page: int = 0,
    has_next: bool = False,
    back_target: str = "order",
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for supplier in suppliers:
        builder.button(
            text=f"🏭 {supplier.name[:51]}",
            callback_data=InspectionChoiceCallback(kind="supplier", entity_id=supplier.id),
        )
    if page > 0:
        builder.button(
            text="◀️ Назад",
            callback_data=InspectionActionCallback(action=f"supplier_page_{page - 1}"),
        )
    if has_next:
        builder.button(
            text="Вперёд ▶️",
            callback_data=InspectionActionCallback(action=f"supplier_page_{page + 1}"),
        )
    builder.button(
        text="✍️ Ввести нового поставщика",
        callback_data=InspectionActionCallback(action="supplier_manual"),
    )
    builder.button(
        text="⬅️ К проверке" if back_target == "confirm" else "⬅️ Назад",
        callback_data=InspectionActionCallback(action=f"back_{back_target}"),
    )
    builder.button(text="✖️ Отменить", callback_data=InspectionActionCallback(action="cancel"))
    builder.adjust(1)
    return builder.as_markup()


def status_keyboard(
    statuses: list[InspectionStatus],
    *,
    back_target: str = "order",
    labels: dict[int, str] | None = None,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for status in statuses:
        builder.button(
            text=f"{status_icon(status.name)} {(labels or {}).get(status.id, status.name)}",
            callback_data=InspectionChoiceCallback(kind="status", entity_id=status.id),
        )
    builder.button(
        text="⬅️ К проверке" if back_target == "confirm" else "⬅️ Назад",
        callback_data=InspectionActionCallback(action=f"back_{back_target}"),
    )
    builder.button(text="✖️ Отменить", callback_data=InspectionActionCallback(action="cancel"))
    builder.adjust(1)
    return builder.as_markup()


def related_orders_keyboard(orders: list[dict[str, str]]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text="➕ Добавить ещё заказ",
        callback_data=InspectionActionCallback(action="related_add"),
    )
    for index, order in enumerate(orders):
        builder.button(
            text=f"🗑 Убрать {order['raw'][:35]}",
            callback_data=InspectionActionCallback(action=f"related_remove_{index}"),
        )
    builder.button(
        text="✅ Готово",
        callback_data=InspectionActionCallback(action="related_done"),
    )
    builder.button(
        text="⬅️ К статусу",
        callback_data=InspectionActionCallback(action="back_status"),
    )
    builder.button(text="✖️ Отменить", callback_data=InspectionActionCallback(action="cancel"))
    builder.adjust(1)
    return builder.as_markup()


def project_keyboard(
    projects: list[Project], *, back_target: str = "status"
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for project in projects:
        builder.button(
            text=f"{project_icon(project.export_group)} {project.name}",
            callback_data=InspectionChoiceCallback(kind="project", entity_id=project.id),
        )
    builder.button(
        text="⬅️ К проверке" if back_target == "confirm" else "⬅️ Назад",
        callback_data=InspectionActionCallback(action=f"back_{back_target}"),
    )
    builder.button(text="✖️ Отменить", callback_data=InspectionActionCallback(action="cancel"))
    builder.adjust(1)
    return builder.as_markup()


def comment_keyboard(*, back_target: str = "rate") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⏭ Без комментария",
                    callback_data=InspectionActionCallback(action="comment_skip").pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ К проверке" if back_target == "confirm" else "⬅️ Назад",
                    callback_data=InspectionActionCallback(action=f"back_{back_target}").pack(),
                ),
                InlineKeyboardButton(
                    text="✖️ Отменить",
                    callback_data=InspectionActionCallback(action="cancel").pack(),
                ),
            ],
        ]
    )


def confirmation_keyboard(
    *,
    supports_related_orders: bool = False,
    has_vehicle_number: bool = False,
    allow_project_edit: bool = True,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Сохранить", callback_data=InspectionActionCallback(action="save"))
    for field, label in (
        ("date", "📅 Дата"),
        ("order", "📦 Заказ"),
        ("status", "📌 Статус"),
        ("related", "🔗 Связанные заказы"),
        ("vehicle", "🚚 Номер авто"),
        ("project", "🧭 Проект"),
        ("rate", "💳 Ставка"),
        ("comment", "💬 Комментарий"),
    ):
        if not supports_related_orders and field == "related":
            continue
        if not has_vehicle_number and field == "vehicle":
            continue
        if not allow_project_edit and field == "project":
            continue
        builder.button(
            text=f"✏️ {label}", callback_data=InspectionActionCallback(action=f"edit_{field}")
        )
    builder.button(text="✖️ Отменить", callback_data=InspectionActionCallback(action="cancel"))
    builder.adjust(1, 2, 2, 2, 1, 1)
    return builder.as_markup()


def my_month_summary_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📦 Показать заказы",
                    callback_data=MyMonthCallback(action="list", page=0).pack(),
                )
            ]
        ]
    )


def my_inspections_keyboard(
    inspections: list[Inspection], *, page: int, has_next: bool
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for inspection in inspections:
        orders = ", ".join(item.order_number_raw for item in inspection.order_numbers)
        builder.button(
            text=f"📦 {orders[:42]} · {inspection.inspection_date:%d.%m}",
            callback_data=MyMonthCallback(action="view", inspection_id=inspection.id, page=page),
        )
    if page > 0:
        builder.button(
            text="◀️ Назад",
            callback_data=MyMonthCallback(action="list", page=page - 1),
        )
    if has_next:
        builder.button(
            text="Вперёд ▶️",
            callback_data=MyMonthCallback(action="list", page=page + 1),
        )
    builder.button(text="✖️ Закрыть", callback_data=MyMonthCallback(action="close"))
    builder.adjust(1)
    return builder.as_markup()


def my_inspection_actions_keyboard(
    inspection_id: int, *, page: int, editable: bool
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if editable:
        rows.append(
            [
                InlineKeyboardButton(
                    text="✏️ Изменить запись",
                    callback_data=MyMonthCallback(
                        action="edit", inspection_id=inspection_id, page=page
                    ).pack(),
                )
            ]
        )
        rows.append(
            [
                InlineKeyboardButton(
                    text="🗑 Удалить запись",
                    callback_data=MyMonthCallback(
                        action="delete", inspection_id=inspection_id, page=page
                    ).pack(),
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                text="⬅️ К заказам",
                callback_data=MyMonthCallback(action="list", page=page).pack(),
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def my_inspection_delete_keyboard(inspection_id: int, *, page: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🗑 Да, удалить",
                    callback_data=MyMonthCallback(
                        action="delete_confirm", inspection_id=inspection_id, page=page
                    ).pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ Отмена",
                    callback_data=MyMonthCallback(
                        action="view", inspection_id=inspection_id, page=page
                    ).pack(),
                )
            ],
        ]
    )


def return_to_orders_keyboard(*, page: int = 0) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📦 Вернуться к заказам",
                    callback_data=MyMonthCallback(action="list", page=max(page, 0)).pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="🏠 В основное меню",
                    callback_data=BatchCallback(action="menu").pack(),
                )
            ],
        ]
    )


def after_save_keyboard(
    inspection_date: date,
    supplier_id: int | None,
    work_type: InspectionWorkType,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🆕 Добавить ещё инспекцию",
                    callback_data=BatchCallback(action="new").pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="🏠 В основное меню",
                    callback_data=BatchCallback(action="menu").pack(),
                )
            ],
        ]
    )


def period_keyboard(purpose: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for choice, label in (
        ("current", "🗓 Текущий месяц"),
        ("today", "📍 Сегодня"),
        ("previous", "◀️ Прошлый месяц"),
        ("custom", "✏️ Выбрать период"),
    ):
        builder.button(text=label, callback_data=PeriodCallback(purpose=purpose, choice=choice))
    builder.button(
        text="✖️ Закрыть",
        callback_data=ReportActionCallback(action="cancel", purpose=purpose),
    )
    builder.adjust(2, 2, 1)
    return builder.as_markup()


def custom_period_keyboard(purpose: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⬅️ К периодам",
                    callback_data=ReportActionCallback(action="period", purpose=purpose).pack(),
                ),
                InlineKeyboardButton(
                    text="✖️ Закрыть",
                    callback_data=ReportActionCallback(action="cancel", purpose=purpose).pack(),
                ),
            ]
        ]
    )


def expert_filter_keyboard(
    purpose: str,
    experts: list[User],
    *,
    page: int = 0,
    has_next: bool = False,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text="👥 Все сотрудники", callback_data=ExpertFilterCallback(purpose=purpose, expert_id=0)
    )
    for expert in experts:
        builder.button(
            text=f"👤 {expert.full_name[:51]}",
            callback_data=ExpertFilterCallback(purpose=purpose, expert_id=expert.id),
        )
    if page > 0:
        builder.button(
            text="◀️ Назад",
            callback_data=ExpertPageCallback(purpose=purpose, page=page - 1),
        )
    if has_next:
        builder.button(
            text="Вперёд ▶️",
            callback_data=ExpertPageCallback(purpose=purpose, page=page + 1),
        )
    builder.button(
        text="⬅️ К выбору периода",
        callback_data=ReportActionCallback(action="period", purpose=purpose),
    )
    builder.button(
        text="✖️ Закрыть",
        callback_data=ReportActionCallback(action="cancel", purpose=purpose),
    )
    builder.adjust(1)
    return builder.as_markup()


def export_mode_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🆕 Только невыгруженные",
                    callback_data=ExportModeCallback(mode=ExportMode.NEW.value).pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="📚 Все за период",
                    callback_data=ExportModeCallback(mode=ExportMode.FULL.value).pack(),
                )
            ],
        ]
    )


def failed_exports_keyboard(batches: list[ExportBatch]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for batch in batches:
        mode = "новые" if batch.mode is ExportMode.NEW else "все"
        builder.button(
            text=(
                f"♻️ Пакет #{batch.id}: {batch.period_from:%d.%m.%Y}–"
                f"{batch.period_to:%d.%m.%Y}, {mode}"
            ),
            callback_data=ExportRetryCallback(batch_id=batch.id),
        )
    builder.adjust(1)
    return builder.as_markup()


def retry_export_keyboard(batch_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"♻️ Повторить пакет #{batch_id}",
                    callback_data=ExportRetryCallback(batch_id=batch_id).pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ К экспертам",
                    callback_data=ReportActionCallback(action="experts", purpose="export").pack(),
                ),
                InlineKeyboardButton(
                    text="✖️ Закрыть",
                    callback_data=ReportActionCallback(action="cancel", purpose="export").pack(),
                ),
            ],
        ]
    )


def admin_menu_keyboard(
    *, pending_users: int = 0, pending_suppliers: int = 0
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for section, label in (
        ("pending_users", f"👥 Заявки · {pending_users}"),
        ("users", "👤 Пользователи"),
        ("status", "📌 Статусы"),
        ("project", "🧭 Проекты"),
        ("reference_upload", "📥 Загрузить данные для сверки"),
    ):
        builder.button(text=label, callback_data=AdminCallback(section=section))
    builder.adjust(2)
    return builder.as_markup()


def admin_home_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🏠 В управление", callback_data=AdminCallback(section="home").pack()
                )
            ]
        ]
    )


def directory_list_keyboard(
    kind: str,
    entities: list[object],
    *,
    page: int = 0,
    page_size: int = 20,
    allow_add: bool = True,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if allow_add:
        builder.button(
            text="➕ Добавить значение",
            callback_data=DirectoryCallback(kind=kind, action="add"),
        )
    start = page * page_size
    for entity in entities[start : start + page_size]:
        marker = "✅"
        if kind == "supplier":
            marker = "✅" if entity.status.value == "active" else "⏸"
        elif not entity.active:
            marker = "⏸"
        builder.button(
            text=f"{marker} {entity.name[:50]}",
            callback_data=DirectoryCallback(kind=kind, action="view", entity_id=entity.id),
        )
    if page > 0:
        builder.button(
            text="◀️ Назад",
            callback_data=DirectoryCallback(kind=kind, action=f"page_{page - 1}"),
        )
    if start + page_size < len(entities):
        builder.button(
            text="Вперёд ▶️",
            callback_data=DirectoryCallback(kind=kind, action=f"page_{page + 1}"),
        )
    builder.button(text="🏠 В управление", callback_data=AdminCallback(section="home"))
    builder.adjust(1)
    return builder.as_markup()


def directory_actions_keyboard(
    kind: str, entity_id: int, *, active: bool, pending: bool = False
) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text="✏️ Переименовать",
                callback_data=DirectoryCallback(
                    kind=kind, action="rename", entity_id=entity_id
                ).pack(),
            )
        ],
        [
            InlineKeyboardButton(
                text="⬅️ К списку",
                callback_data=AdminCallback(section=kind).pack(),
            ),
            InlineKeyboardButton(
                text="🏠 Управление",
                callback_data=AdminCallback(section="home").pack(),
            ),
        ],
        [
            InlineKeyboardButton(
                text="⏸ Деактивировать" if active else "▶️ Активировать",
                callback_data=DirectoryCallback(
                    kind=kind, action="disable" if active else "enable", entity_id=entity_id
                ).pack(),
            )
        ],
    ]
    if kind == "supplier" and pending:
        rows.append(
            [
                InlineKeyboardButton(
                    text="🗑 Отклонить",
                    callback_data=DirectoryCallback(
                        kind="supplier", action="reject", entity_id=entity_id
                    ).pack(),
                )
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def status_duplicate_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Да, разрешает",
                    callback_data=DirectoryCallback(kind="status", action="duplicate_yes").pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="🏠 Отменить и выйти",
                    callback_data=AdminCallback(section="home").pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="🚫 Нет, запрещает",
                    callback_data=DirectoryCallback(kind="status", action="duplicate_no").pack(),
                )
            ],
        ]
    )


def project_group_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for group in ExportGroup:
        builder.button(
            text=f"{project_icon(group)} Лист {group.value}",
            callback_data=DirectoryCallback(kind="project", action=f"group_{group.name.lower()}"),
        )
    builder.button(text="🏠 Отменить и выйти", callback_data=AdminCallback(section="home"))
    builder.adjust(3, 1)
    return builder.as_markup()


def pending_supplier_keyboard(supplier_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Подтвердить",
                    callback_data=DirectoryCallback(
                        kind="supplier", action="approve", entity_id=supplier_id
                    ).pack(),
                ),
                InlineKeyboardButton(
                    text="🔗 Объединить с другим",
                    callback_data=DirectoryCallback(
                        kind="supplier", action="merge", entity_id=supplier_id
                    ).pack(),
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🗑 Отклонить",
                    callback_data=DirectoryCallback(
                        kind="supplier", action="reject", entity_id=supplier_id
                    ).pack(),
                )
            ],
        ]
    )


def reject_supplier_confirmation_keyboard(supplier_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🗑 Да, отклонить",
                    callback_data=DirectoryCallback(
                        kind="supplier", action="reject_do", entity_id=supplier_id
                    ).pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ Отмена",
                    callback_data=AdminCallback(section="pending_suppliers").pack(),
                )
            ],
        ]
    )


def merge_targets_keyboard(source_id: int, suppliers: list[Supplier]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for supplier in suppliers:
        if supplier.id != source_id:
            builder.button(
                text=f"🏭 {supplier.name[:51]}",
                callback_data=DirectoryCallback(
                    kind="supplier", action=f"merge_pick_{source_id}", entity_id=supplier.id
                ),
            )
    builder.button(text="⬅️ К поставщикам", callback_data=AdminCallback(section="pending_suppliers"))
    builder.button(text="🏠 Управление", callback_data=AdminCallback(section="home"))
    builder.adjust(1)
    return builder.as_markup()


def merge_confirmation_keyboard(source_id: int, target_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔗 Да, объединить",
                    callback_data=DirectoryCallback(
                        kind="supplier", action=f"merge_do_{source_id}", entity_id=target_id
                    ).pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ Выбрать другого",
                    callback_data=DirectoryCallback(
                        kind="supplier", action="merge", entity_id=source_id
                    ).pack(),
                ),
                InlineKeyboardButton(
                    text="🏠 Управление",
                    callback_data=AdminCallback(section="home").pack(),
                ),
            ],
        ]
    )


def user_management_keyboard(
    users: list[User],
    *,
    page: int = 0,
    has_next: bool = False,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for user in users:
        action = "view"
        marker = {
            UserStatus.ACTIVE: "🟢",
            UserStatus.BLOCKED: "🔴",
            UserStatus.PENDING: "🟡",
            UserStatus.REJECTED: "⚪️",
        }[user.status]
        builder.button(
            text=f"{marker} {user.full_name[:45]}",
            callback_data=UserManageCallback(action=action, user_id=user.id),
        )
    if page > 0:
        builder.button(
            text="◀️ Назад",
            callback_data=AdminPageCallback(section="users", page=page - 1),
        )
    if has_next:
        builder.button(
            text="Вперёд ▶️",
            callback_data=AdminPageCallback(section="users", page=page + 1),
        )
    builder.button(text="🏠 В управление", callback_data=AdminCallback(section="home"))
    builder.adjust(1)
    return builder.as_markup()


def user_actions_keyboard(user: User) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if user.role in {UserRole.EXPERT, UserRole.SPECIALIST}:
        target = UserRole.SPECIALIST if user.role is UserRole.EXPERT else UserRole.EXPERT
        label = (
            "📋 Сделать специалистом" if target is UserRole.SPECIALIST else "🦺 Сделать экспертом"
        )
        rows.append(
            [
                InlineKeyboardButton(
                    text=label,
                    callback_data=UserManageCallback(
                        action=f"role_confirm_{target.value}", user_id=user.id
                    ).pack(),
                )
            ]
        )
        if user.status is UserStatus.ACTIVE:
            rows.append(
                [
                    InlineKeyboardButton(
                        text="⚙️ Сделать администратором",
                        callback_data=UserManageCallback(
                            action=f"role_confirm_{UserRole.ADMIN.value}", user_id=user.id
                        ).pack(),
                    )
                ]
            )
    if user.status == UserStatus.ACTIVE:
        rows.append(
            [
                InlineKeyboardButton(
                    text="⛔ Заблокировать",
                    callback_data=UserManageCallback(
                        action="block_confirm", user_id=user.id
                    ).pack(),
                )
            ]
        )
    elif user.status == UserStatus.BLOCKED and user.role is not None:
        rows.append(
            [
                InlineKeyboardButton(
                    text="✅ Разблокировать",
                    callback_data=UserManageCallback(action="unblock", user_id=user.id).pack(),
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                text="⬅️ К пользователям", callback_data=AdminCallback(section="users").pack()
            ),
            InlineKeyboardButton(
                text="🏠 Управление", callback_data=AdminCallback(section="home").pack()
            ),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def user_role_confirm_keyboard(user_id: int, role: UserRole) -> InlineKeyboardMarkup:
    label = {
        UserRole.SPECIALIST: "специалистом",
        UserRole.EXPERT: "экспертом",
        UserRole.ADMIN: "администратором",
    }[role]
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"✅ Да, сделать {label}",
                    callback_data=UserManageCallback(
                        action=f"role_set_{role.value}", user_id=user_id
                    ).pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ Отмена",
                    callback_data=UserManageCallback(action="view", user_id=user_id).pack(),
                )
            ],
        ]
    )


def user_block_confirm_keyboard(user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⛔ Да, заблокировать",
                    callback_data=UserManageCallback(action="block", user_id=user_id).pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ Не блокировать",
                    callback_data=UserManageCallback(action="view", user_id=user_id).pack(),
                )
            ],
        ]
    )


def admin_list_pagination_keyboard(
    section: str,
    *,
    page: int,
    has_next: bool,
) -> InlineKeyboardMarkup:
    buttons: list[InlineKeyboardButton] = []
    if page > 0:
        buttons.append(
            InlineKeyboardButton(
                text="⬅️ Предыдущие",
                callback_data=AdminPageCallback(section=section, page=page - 1).pack(),
            )
        )
    if has_next:
        buttons.append(
            InlineKeyboardButton(
                text="Следующие ➡️",
                callback_data=AdminPageCallback(section=section, page=page + 1).pack(),
            )
        )
    rows = [buttons] if buttons else []
    rows.append(
        [
            InlineKeyboardButton(
                text="🏠 В управление", callback_data=AdminCallback(section="home").pack()
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)
