"""
app/repositories/base.py
─────────────────────────
Generic async repository providing standard CRUD operations.
All concrete repositories extend this class.
Follows the Repository Pattern – callers never touch SQLAlchemy directly.
"""

from typing import Any, Generic, TypeVar

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import Base

ModelT = TypeVar("ModelT", bound=Base)


class BaseRepository(Generic[ModelT]):
    """
    Thread-safe async CRUD repository.

    Subclasses must set `model` to their ORM class:

        class EmployeeRepository(BaseRepository[Employee]):
            model = Employee
    """

    model: type[ModelT]

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, record_id: int) -> ModelT | None:
        return await self._session.get(self.model, record_id)

    async def get_all(self, limit: int = 100, offset: int = 0) -> list[ModelT]:
        result = await self._session.execute(
            select(self.model).limit(limit).offset(offset)
        )
        return list(result.scalars().all())

    async def create(self, obj: ModelT) -> ModelT:
        self._session.add(obj)
        await self._session.flush()  # get generated PK without committing
        await self._session.refresh(obj)
        return obj

    async def update(self, obj: ModelT, data: dict[str, Any]) -> ModelT:
        for field, value in data.items():
            setattr(obj, field, value)
        await self._session.flush()
        await self._session.refresh(obj)
        return obj

    async def delete(self, obj: ModelT) -> None:
        await self._session.delete(obj)
        await self._session.flush()
