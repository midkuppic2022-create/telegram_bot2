from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ReferenceOrder, ReferenceUpload


class ReconciliationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_active_upload(self, *, for_update: bool = False) -> ReferenceUpload | None:
        statement = (
            select(ReferenceUpload)
            .where(ReferenceUpload.active.is_(True))
            .order_by(ReferenceUpload.id.desc())
            .limit(1)
        )
        if for_update:
            statement = statement.with_for_update()
        return await self.session.scalar(statement)

    async def deactivate_all(self) -> None:
        await self.session.execute(
            update(ReferenceUpload)
            .where(ReferenceUpload.active.is_(True))
            .values(active=False)
        )

    async def find_order(
        self, upload_id: int, normalized_order: str
    ) -> ReferenceOrder | None:
        return await self.session.scalar(
            select(ReferenceOrder).where(
                ReferenceOrder.upload_id == upload_id,
                ReferenceOrder.order_number_normalized == normalized_order,
            )
        )

    def add(self, upload: ReferenceUpload) -> None:
        self.session.add(upload)

    async def flush(self) -> None:
        await self.session.flush()
