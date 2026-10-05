from datetime import date
from decimal import Decimal

from app.enums import ExportGroup, SupplierStatus, UserRole, UserStatus
from app.validators import normalize_name

ROLE_LABELS = {
    UserRole.EXPERT: "🦺 Эксперт",
    UserRole.SPECIALIST: "📋 Специалист",
    UserRole.ADMIN: "🛡 Администратор",
}

USER_STATUS_LABELS = {
    UserStatus.PENDING: "🟡 Ожидает решения",
    UserStatus.ACTIVE: "🟢 Активен",
    UserStatus.REJECTED: "⚪️ Отклонён",
    UserStatus.BLOCKED: "🔴 Заблокирован",
}

SUPPLIER_STATUS_LABELS = {
    SupplierStatus.PENDING: "🟡 На проверке",
    SupplierStatus.ACTIVE: "🟢 Активен",
    SupplierStatus.INACTIVE: "⚪️ Неактивен",
}

STATUS_ICONS = {
    "осмотр+погрузка": "🔎🚚",
    "осмотр": "🔎",
    "погрузка": "🚚",
    "стоп": "⛔",
    "отмена": "✖️",
    "холостой выезд": "🛣",
    "комиссионная инспекция": "👥",
    "повторная инспекция": "🔁",
    "отгружен с другим заказом": "🔀",
}

PROJECT_ICONS = {
    ExportGroup.AK: "🔷",
    ExportGroup.SV: "🟢",
    ExportGroup.X5: "🟠",
}


def role_label(role: UserRole | None) -> str:
    return ROLE_LABELS.get(role, "👤 Роль не назначена")


def user_status_label(status: UserStatus) -> str:
    return USER_STATUS_LABELS[status]


def supplier_status_label(status: SupplierStatus) -> str:
    return SUPPLIER_STATUS_LABELS[status]


def status_icon(name: str) -> str:
    return STATUS_ICONS.get(normalize_name(name), "📌")


def project_icon(group: ExportGroup) -> str:
    return PROJECT_ICONS[group]


def format_amount(value: Decimal | str) -> str:
    amount = value if isinstance(value, Decimal) else Decimal(value)
    return f"{amount:,.2f}".replace(",", " ").replace(".", ",")


def format_period(start: date, end: date) -> str:
    if start == end:
        return start.strftime("%d.%m.%Y")
    return f"{start:%d.%m.%Y} — {end:%d.%m.%Y}"


def plural_ru(value: int, one: str, few: str, many: str) -> str:
    remainder_100 = value % 100
    remainder_10 = value % 10
    if 11 <= remainder_100 <= 14:
        form = many
    elif remainder_10 == 1:
        form = one
    elif 2 <= remainder_10 <= 4:
        form = few
    else:
        form = many
    return f"{value} {form}"


def inspection_count(value: int) -> str:
    return plural_ru(value, "инспекция", "инспекции", "инспекций")
