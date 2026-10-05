from sqlalchemy.ext.asyncio import AsyncSession

from app.enums import UserRole, UserStatus
from app.errors import EntityNotFound
from app.models import RegistrationAdminNotification, User
from app.repositories.users import UserRepository
from app.validators import validate_full_name


class UserService:
    def __init__(self, session: AsyncSession) -> None:
        self.repository = UserRepository(session)

    async def get_by_telegram_id(self, telegram_id: int) -> User | None:
        return await self.repository.get_by_telegram_id(telegram_id)

    async def get(self, user_id: int) -> User:
        user = await self.repository.get(user_id)
        if user is None:
            raise EntityNotFound("Пользователь не найден.")
        return user

    async def sync_telegram_profile(
        self, *, telegram_id: int, username: str | None, telegram_name: str
    ) -> User | None:
        """Keep Telegram metadata fresh without overwriting a chosen registration name."""
        user = await self.get_by_telegram_id(telegram_id)
        if user is None:
            return None
        user.username = username
        placeholder = f"Администратор {telegram_id}"
        if user.full_name == placeholder and len(telegram_name.strip()) >= 2:
            user.full_name = validate_full_name(telegram_name)
        await self.repository.flush()
        return user

    async def register_pending(
        self,
        *,
        telegram_id: int,
        username: str | None,
        full_name: str,
        requested_role: UserRole,
    ) -> User:
        cleaned_name = validate_full_name(full_name)
        user = await self.get_by_telegram_id(telegram_id)
        if user is None:
            user = User(
                telegram_id=telegram_id,
                username=username,
                full_name=cleaned_name,
                requested_role=requested_role,
                status=UserStatus.PENDING,
            )
            self.repository.add(user)
        elif user.role is not UserRole.ADMIN:
            user.username = username
            user.full_name = cleaned_name
            user.requested_role = requested_role
            user.role = None
            user.status = UserStatus.PENDING
        await self.repository.flush()
        await self.repository.clear_registration_notifications(user.id)
        await self.repository.flush()
        return user

    async def bootstrap_admins(self, telegram_ids: list[int]) -> None:
        existing = {
            user.telegram_id: user
            for user in await self.repository.get_by_telegram_ids(telegram_ids)
        }
        for telegram_id in telegram_ids:
            user = existing.get(telegram_id)
            if user is None:
                self.repository.add(
                    User(
                        telegram_id=telegram_id,
                        full_name=f"Администратор {telegram_id}",
                        requested_role=UserRole.ADMIN,
                        role=UserRole.ADMIN,
                        status=UserStatus.ACTIVE,
                    )
                )
            else:
                user.requested_role = UserRole.ADMIN
                user.role = UserRole.ADMIN
                user.status = UserStatus.ACTIVE
        await self.repository.flush()

    async def list_pending(self, *, limit: int | None = None, offset: int = 0) -> list[User]:
        return await self.repository.list_pending(limit=limit, offset=offset)

    async def list_for_reports(self, *, limit: int | None = None, offset: int = 0) -> list[User]:
        return await self.repository.list_for_reports(limit=limit, offset=offset)

    async def list_active_admins(self) -> list[User]:
        return await self.repository.list_active_admins()

    async def list_managed(self, *, limit: int | None = None, offset: int = 0) -> list[User]:
        return await self.repository.list_managed(limit=limit, offset=offset)

    async def approve(self, user_id: int, role: UserRole) -> User:
        if role not in {UserRole.EXPERT, UserRole.SPECIALIST}:
            raise ValueError("Only expert and specialist roles can be approved from registration")
        user = await self.repository.get_for_update(user_id)
        if user is None:
            raise EntityNotFound("Пользователь не найден.")
        if user.status is not UserStatus.PENDING:
            raise ValueError("Эта заявка уже обработана.")
        user.role = role
        user.requested_role = role
        user.status = UserStatus.ACTIVE
        await self.repository.flush()
        return user

    async def reject(self, user_id: int) -> User:
        user = await self.repository.get_for_update(user_id)
        if user is None:
            raise EntityNotFound("Пользователь не найден.")
        if user.status is not UserStatus.PENDING:
            raise ValueError("Эта заявка уже обработана.")
        user.status = UserStatus.REJECTED
        user.role = None
        await self.repository.flush()
        return user

    async def record_registration_notification(
        self, *, user_id: int, admin_chat_id: int, telegram_message_id: int
    ) -> None:
        await self.repository.add_registration_notification(
            user_id=user_id,
            admin_chat_id=admin_chat_id,
            telegram_message_id=telegram_message_id,
        )
        await self.repository.flush()

    async def list_registration_notifications(
        self, user_id: int
    ) -> list[RegistrationAdminNotification]:
        return await self.repository.list_registration_notifications(user_id)

    async def set_blocked(self, user_id: int, blocked: bool) -> User:
        user = await self.get(user_id)
        if user.role is UserRole.ADMIN:
            raise ValueError("Администратора нельзя заблокировать из меню.")
        if not blocked and user.role is None:
            raise ValueError("Сначала назначьте пользователю роль через заявку.")
        user.status = UserStatus.BLOCKED if blocked else UserStatus.ACTIVE
        await self.repository.flush()
        return user

    async def change_role(self, user_id: int, role: UserRole) -> User:
        if role not in {UserRole.EXPERT, UserRole.SPECIALIST, UserRole.ADMIN}:
            raise ValueError("Неизвестная роль пользователя.")
        user = await self.get(user_id)
        if user.role is UserRole.ADMIN:
            raise ValueError("Роль администратора нельзя изменить из меню.")
        if role is UserRole.ADMIN and user.status is not UserStatus.ACTIVE:
            raise ValueError("Администратором можно сделать только активного пользователя.")
        if user.status not in {UserStatus.ACTIVE, UserStatus.BLOCKED}:
            raise ValueError("Сначала одобрите заявку пользователя.")
        user.role = role
        user.requested_role = role
        await self.repository.flush()
        return user
