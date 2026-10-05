import re
import unicodedata
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from app.errors import DomainError

ORDER_RE = re.compile(r"^[A-Za-z0-9]{1,64}$")
MONEY_RE = re.compile(r"^\d+(?:[.,]\d{1,2})?$")
DATE_RANGE_RE = re.compile(r"^\s*(\d{2}\.\d{2}\.\d{4})\s*(?:-|–|—)\s*(\d{2}\.\d{2}\.\d{4})\s*$")
MAX_MONEY = Decimal("9999999999.99")


def normalize_name(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value)
    return " ".join(normalized.split()).casefold()


def clean_display_name(value: str, *, max_length: int = 255) -> str:
    cleaned = " ".join(unicodedata.normalize("NFKC", value).split())
    if not cleaned:
        raise DomainError("Значение не может быть пустым.")
    if len(cleaned) > max_length:
        raise DomainError(f"Максимальная длина — {max_length} символов.")
    return cleaned


def validate_full_name(value: str) -> str:
    cleaned = clean_display_name(value, max_length=160)
    if len(cleaned) < 2:
        raise DomainError("Укажите ФИО или понятное рабочее имя.")
    return cleaned


def validate_order_number(value: str) -> tuple[str, str]:
    if value != value.strip() or not ORDER_RE.fullmatch(value):
        raise DomainError(
            "Номер заказа должен содержать только латинские буквы и цифры, без пробелов."
        )
    return value, value.upper()


def parse_inspection_date(value: str, *, today: date | None = None) -> date:
    try:
        parsed = datetime.strptime(value.strip(), "%d.%m.%Y").date()
    except ValueError as exc:
        raise DomainError("Введите дату в формате ДД.ММ.ГГГГ.") from exc
    current = today or date.today()
    if parsed > current:
        raise DomainError("Дата инспекции не может быть в будущем.")
    return parsed


def parse_date_range(value: str, *, today: date | None = None) -> tuple[date, date]:
    match = DATE_RANGE_RE.fullmatch(value)
    if not match:
        raise DomainError("Введите период: ДД.ММ.ГГГГ — ДД.ММ.ГГГГ.")
    start = parse_inspection_date(match.group(1), today=today)
    end = parse_inspection_date(match.group(2), today=today)
    if start > end:
        raise DomainError("Начало периода не может быть позже окончания.")
    return start, end


def parse_money(value: str) -> Decimal:
    stripped = value.strip()
    if not MONEY_RE.fullmatch(stripped):
        raise DomainError("Введите неотрицательную сумму, максимум с двумя знаками после запятой.")
    try:
        amount = Decimal(stripped.replace(",", ".")).quantize(Decimal("0.01"))
    except InvalidOperation as exc:
        raise DomainError("Не удалось распознать сумму.") from exc
    if amount > MAX_MONEY:
        raise DomainError("Сумма слишком большая.")
    return amount


def clean_comment(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    if not cleaned:
        return None
    if len(cleaned) > 2000:
        raise DomainError("Комментарий не должен превышать 2000 символов.")
    return cleaned
