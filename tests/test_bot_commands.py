from app.bot_commands import commands_for_role
from app.enums import UserRole


def command_names(role: UserRole | None) -> set[str]:
    return {command.command for command in commands_for_role(role)}


def test_public_commands_do_not_expose_role_actions() -> None:
    assert command_names(None) == {"start", "cancel", "help"}


def test_commands_are_scoped_by_role() -> None:
    expert = command_names(UserRole.EXPERT)
    specialist = command_names(UserRole.SPECIALIST)
    admin = command_names(UserRole.ADMIN)

    assert {"add", "my"}.issubset(expert)
    assert "export" in expert
    assert {"add", "my", "report", "export"}.issubset(specialist)
    assert {"add", "my", "report", "export", "admin"}.issubset(admin)
