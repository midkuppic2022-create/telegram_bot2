from datetime import date

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Inspection, User


class ReportRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def count_by_expert(
        self, start: date, end: date, *, expert_id: int | None
    ) -> list[tuple[int, str, int]]:
        statement = (
            select(User.id, User.full_name, func.count(Inspection.id))
            .join(Inspection, Inspection.author_id == User.id)
            .where(Inspection.inspection_date.between(start, end))
            .group_by(User.id, User.full_name)
            .order_by(func.count(Inspection.id).desc(), User.full_name)
        )
        if expert_id is not None:
            statement = statement.where(User.id == expert_id)
        rows = (await self.session.execute(statement)).all()
        return [(int(row[0]), str(row[1]), int(row[2])) for row in rows]
