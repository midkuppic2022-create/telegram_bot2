from decimal import Decimal

from app.presentation import format_amount, inspection_count, status_icon


def test_amount_uses_human_readable_russian_format() -> None:
    assert format_amount(Decimal("1500")) == "1 500,00"
    assert format_amount("0.50") == "0,50"


def test_inspection_count_uses_correct_russian_plural() -> None:
    assert inspection_count(1) == "1 инспекция"
    assert inspection_count(2) == "2 инспекции"
    assert inspection_count(5) == "5 инспекций"
    assert inspection_count(11) == "11 инспекций"
    assert inspection_count(21) == "21 инспекция"


def test_status_icon_is_stable_and_has_fallback() -> None:
    assert status_icon("  Повторная   инспекция ") == "🔁"
    assert status_icon("Новый статус") == "📌"
