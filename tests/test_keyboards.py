from datetime import date

from app.enums import InspectionWorkType, UserRole, UserStatus
from app.keyboards import (
    BTN_ADD,
    BTN_ADMIN,
    BTN_EXPORT,
    BTN_REPORT,
    admin_menu_keyboard,
    after_save_keyboard,
    confirmation_keyboard,
    directory_actions_keyboard,
    expert_filter_keyboard,
    main_menu,
    my_inspection_actions_keyboard,
    pending_supplier_keyboard,
    return_to_orders_keyboard,
    supplier_results_keyboard,
    user_actions_keyboard,
    user_management_keyboard,
    user_role_confirm_keyboard,
)
from app.models import Supplier, User


def button_texts(user: User) -> set[str]:
    keyboard = main_menu(user)
    assert keyboard is not None
    return {button.text for row in keyboard.keyboard for button in row}


def make_user(role: UserRole) -> User:
    return User(
        telegram_id=1,
        full_name="Тест",
        requested_role=role,
        role=role,
        status=UserStatus.ACTIVE,
    )


def test_role_menus() -> None:
    expert = make_user(UserRole.EXPERT)
    assert BTN_ADD in button_texts(expert)
    assert BTN_EXPORT in button_texts(expert)
    assert main_menu(expert).is_persistent is True
    specialist_buttons = button_texts(make_user(UserRole.SPECIALIST))
    assert BTN_EXPORT in specialist_buttons
    assert BTN_REPORT in specialist_buttons
    assert BTN_ADD in specialist_buttons
    admin_buttons = button_texts(make_user(UserRole.ADMIN))
    assert {BTN_ADD, BTN_EXPORT, BTN_ADMIN}.issubset(admin_buttons)


def test_admin_menu_uses_reference_upload_instead_of_supplier_directory() -> None:
    keyboard = admin_menu_keyboard(pending_users=2)
    texts = {button.text for row in keyboard.inline_keyboard for button in row}

    assert "📥 Загрузить данные для сверки" in texts
    assert not any("Поставщик" in text or "поставщик" in text for text in texts)


def test_supplier_keyboard_has_pagination_controls() -> None:
    supplier = Supplier(id=1, name="Поставщик", normalized_name="ПОСТАВЩИК")
    keyboard = supplier_results_keyboard([supplier], page=2, has_next=True)
    texts = [button.text for row in keyboard.inline_keyboard for button in row]
    assert "◀️ Назад" in texts
    assert "Вперёд ▶️" in texts


def test_pending_supplier_keyboards_have_reject_action() -> None:
    keyboards = (
        pending_supplier_keyboard(1),
        directory_actions_keyboard("supplier", 1, active=False, pending=True),
    )

    for keyboard in keyboards:
        texts = [button.text for row in keyboard.inline_keyboard for button in row]
        assert "🗑 Отклонить" in texts


def test_expert_and_user_keyboards_have_pagination_controls() -> None:
    user = make_user(UserRole.EXPERT)
    user.id = 1
    expert_keyboard = expert_filter_keyboard("report", [user], page=1, has_next=True)
    managed_keyboard = user_management_keyboard([user], page=1, has_next=True)

    for keyboard in (expert_keyboard, managed_keyboard):
        texts = [button.text for row in keyboard.inline_keyboard for button in row]
        assert "◀️ Назад" in texts
        assert "Вперёд ▶️" in texts


def test_specialist_duplicate_confirmation_does_not_allow_project_change() -> None:
    keyboard = confirmation_keyboard(allow_project_edit=False)
    texts = {button.text for row in keyboard.inline_keyboard for button in row}

    assert "✏️ 🧭 Проект" not in texts
    assert "✏️ 📦 Заказ" in texts


def test_after_save_has_add_and_main_menu_actions() -> None:
    keyboard = after_save_keyboard(
        date(2026, 8, 8), None, InspectionWorkType.SPECIALIST
    )
    texts = {button.text for row in keyboard.inline_keyboard for button in row}

    assert texts == {"🆕 Добавить ещё инспекцию", "🏠 В основное меню"}


def test_saved_inspection_actions_include_delete() -> None:
    keyboard = my_inspection_actions_keyboard(1, page=0, editable=True)
    texts = {button.text for row in keyboard.inline_keyboard for button in row}

    assert "✏️ Изменить запись" in texts
    assert "🗑 Удалить запись" in texts


def test_return_to_orders_keyboard_keeps_requested_page() -> None:
    keyboard = return_to_orders_keyboard(page=3)
    texts = {button.text for row in keyboard.inline_keyboard for button in row}

    assert texts == {"📦 Вернуться к заказам", "🏠 В основное меню"}


def test_active_employee_can_be_promoted_to_admin_from_user_actions() -> None:
    user = make_user(UserRole.EXPERT)
    user.id = 7
    keyboard = user_actions_keyboard(user)
    texts = {button.text for row in keyboard.inline_keyboard for button in row}
    confirmation = user_role_confirm_keyboard(user.id, UserRole.ADMIN)
    confirmation_texts = {
        button.text for row in confirmation.inline_keyboard for button in row
    }

    assert "⚙️ Сделать администратором" in texts
    assert "✅ Да, сделать администратором" in confirmation_texts
