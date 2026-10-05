import pytest

from app.config import Settings


@pytest.mark.parametrize(
    ("raw_ids", "expected"),
    [
        ("1074422369", [1_074_422_369]),
        ("1074422369, 222333444", [1_074_422_369, 222_333_444]),
    ],
)
def test_admin_telegram_ids_are_parsed_from_environment(
    monkeypatch: pytest.MonkeyPatch,
    raw_ids: str,
    expected: list[int],
) -> None:
    monkeypatch.setenv("BOT_TOKEN", "123456789:test_token")
    monkeypatch.setenv("ADMIN_TELEGRAM_IDS", raw_ids)

    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert settings.admin_telegram_ids == expected
