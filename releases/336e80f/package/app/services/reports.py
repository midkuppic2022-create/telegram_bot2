from dataclasses import dataclass
from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.reports import ReportRepository


@dataclass(slots=True, frozen=True)
class ExpertCount:
    user_id: int
    full_name: str
    count: int


class ReportService:
    def __init__(self, session: AsyncSession) -> None:
        self.repository = ReportRepository(session)

    async def count_by_expert(
        self, start: date, end: date, *, expert_id: int | None = None
    ) -> list[ExpertCount]:
        rows = await self.repository.count_by_expert(start, end, expert_id=expert_id)
        return [ExpertCount(user_id=row[0], full_name=row[1], count=row[2]) for row in rows]
