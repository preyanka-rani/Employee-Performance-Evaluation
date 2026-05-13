"""
app/repositories/employee_repository.py
─────────────────────────────────────────
Async data-access layer for the Employee model.
"""

from sqlalchemy import select

from app.models.employee import Employee
from app.repositories.base import BaseRepository


class EmployeeRepository(BaseRepository[Employee]):
    model = Employee

    async def get_by_email(self, email: str) -> Employee | None:
        result = await self._session.execute(
            select(Employee).where(Employee.email == email)
        )
        return result.scalar_one_or_none()

    async def get_by_employee_id(self, employee_id: str) -> Employee | None:
        result = await self._session.execute(
            select(Employee).where(Employee.employee_id == employee_id)
        )
        return result.scalar_one_or_none()

    async def get_by_team(self, team: str, active_only: bool = True) -> list[Employee]:
        stmt = select(Employee).where(Employee.team == team)
        if active_only:
            stmt = stmt.where(Employee.is_active.is_(True))
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get_by_gitlab_username(self, username: str) -> Employee | None:
        result = await self._session.execute(
            select(Employee).where(Employee.gitlab_username == username)
        )
        return result.scalar_one_or_none()
