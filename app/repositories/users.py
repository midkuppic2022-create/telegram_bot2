from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.enums import UserRole, UserStatus
from app.models import RegistrationAdminNotification, User


class UserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, user_id: int) -> User | None:
        return await self.session.get(User, user_id)

    async def get_for_update(self, user_id: int) -> User | None:
        return await self.session.scalar(
            select(User).where(User.id == user_id).with_for_update()
        )

    async def get_by_telegram_id(self, telegram_id: int) -> User | None:
        return await self.session.scalar(select(User).where(User.telegram_id == telegram_id))

    async def get_by_telegram_ids(self, telegram_ids: list[int]) -> list[User]:
        if not telegram_ids:
            return []
        return list(
            (
                await self.session.scalars(select(User).where(User.telegram_id.in_(telegram_ids)))
            ).all()
        )

    def add(self, user: User) -> None:
        self.session.add(user)

    async def clear_registration_notifications(self, user_id: int) -> None:
        await self.session.execute(
            delete(RegistrationAdminNotification).where(
                RegistrationAdminNotification.user_id == user_id
            )
        )

    async def add_registration_notification(
        self, *, user_id: int, admin_chat_id: int, telegram_message_id: int
    ) -> None:
        existing = await self.session.scalar(
            select(RegistrationAdminNotification).where(
                RegistrationAdminNotification.admin_chat_id == admin_chat_id,
                RegistrationAdminNotification.telegram_message_id == telegram_message_id,
            )
        )
        if existing is None:
            self.session.add(
                RegistrationAdminNotification(
                    user_id=user_id,
                    admin_chat_id=admin_chat_id,
                    telegram_message_id=telegram_message_id,
                )
            )

    async def list_registration_notifications(
        self, user_id: int
    ) -> list[RegistrationAdminNotification]:
        return list(
            (
                await self.session.scalars(
                    select(RegistrationAdminNotification)
                    .where(RegistrationAdminNotification.user_id == user_id)
                    .order_by(RegistrationAdminNotification.id)
                )
            ).all()
        )

    async def flush(self) -> None:
        await self.session.flush()

    async def list_pending(self, *, limit: int | None = None, offset: int = 0) -> list[User]:
        statement = (
            select(User)
            .where(User.status == UserStatus.PENDING)
            .order_by(User.created_at, User.id)
            .offset(offset)
        )
        if limit is not None:
            statement = statement.limit(limit)
        return list((await self.session.scalars(statement)).all())

    async def list_for_reports(self, *, limit: int | None = None, offset: int = 0) -> list[User]:
        statement = (
            select(User)
            .where(
                User.status == UserStatus.ACTIVE,
                User.role.in_([UserRole.EXPERT, UserRole.SPECIALIST, UserRole.ADMIN]),
            )
            .order_by(User.full_name, User.id)
            .offset(offset)
        )
        if limit is not None:
            statement = statement.limit(limit)
        return list((await self.session.scalars(statement)).all())

    async def list_active_admins(self) -> list[User]:
        return list(
            (
                await self.session.scalars(
                    select(User)
                    .where(
                        User.role == UserRole.ADMIN,
                        User.status == UserStatus.ACTIVE,
                    )
                    .order_by(User.id)
                )
            ).all()
        )

    async def list_managed(self, *, limit: int | None = None, offset: int = 0) -> list[User]:
        statement = (
            select(User)
            .where(or_(User.role.is_(None), User.role != UserRole.ADMIN))
            .order_by(User.full_name, User.id)
            .offset(offset)
        )
        if limit is not None:
            statement = statement.limit(limit)
        return list((await self.session.scalars(statement)).all())
