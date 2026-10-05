from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.enums import UserRole, UserStatus
from app.models import User


class UserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, user_id: int) -> User | None:
        return await self.session.get(User, user_id)

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
