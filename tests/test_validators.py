from datetime import date
from decimal import Decimal

import pytest

from app.errors import DomainError
from app.validators import (
    clean_comment,
    normalize_name,
    parse_date_range,
    parse_inspection_date,
    parse_money,
    validate_order_number,
)


@pytest.mark.parametrize(
    ("value", "raw", "normalized"),
    [
        ("YUG23000Y8738378", "YUG23000Y8738378", "YUG23000Y8738378"),
        ("cnt23000y8728356", "cnt23000y8728356", "CNT23000Y8728356"),
        ("12345", "12345", "12345"),
    ],
)
def test_order_number_validation(value: str, raw: str, normalized: str) -> None:
    assert validate_order_number(value) == (raw, normalized)


@pytest.mark.parametrize("value", ["", " YUG1", "YUG1 ", "YUG 1", "УUG1", "ABC-1"])
def test_order_number_rejects_invalid_input(value: str) -> None:
    with pytest.raises(DomainError):
        validate_order_number(value)


def test_date_and_range_validation() -> None:
    today = date(2026, 7, 14)
    assert parse_inspection_date("13.07.2026", today=today) == date(2026, 7, 13)
    assert parse_inspection_date("13.07", today=today) == date(2026, 7, 13)
    assert parse_inspection_date("13,07", today=today) == date(2026, 7, 13)
    assert parse_inspection_date("13,07,2026", today=today) == date(2026, 7, 13)
    assert parse_date_range("01.07.2026 — 14.07.2026", today=today) == (
        date(2026, 7, 1),
        today,
    )
    with pytest.raises(DomainError):
        parse_inspection_date("15.07.2026", today=today)
    with pytest.raises(DomainError):
        parse_date_range("14.07.2026 - 01.07.2026", today=today)


@pytest.mark.parametrize(
    ("value", "expected"),
    [("0", Decimal("0.00")), ("1500", Decimal("1500.00")), ("1500,5", Decimal("1500.50"))],
)
def test_money_validation(value: str, expected: Decimal) -> None:
    assert parse_money(value) == expected


@pytest.mark.parametrize("value", ["-1", "1.234", "1e3", "abc", ""])
def test_money_rejects_invalid_input(value: str) -> None:
    with pytest.raises(DomainError):
        parse_money(value)


def test_text_normalization() -> None:
    assert normalize_name("  Гринхаус  ООО ") == "гринхаус ооо"
    assert clean_comment("   ") is None
    assert clean_comment("  готово  ") == "готово"
