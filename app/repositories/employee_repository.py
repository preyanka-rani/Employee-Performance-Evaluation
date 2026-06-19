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

    # ── NEW: Smart Upsert Logic to prevent UNIQUE constraint failures ──
    async def upsert_by_email(
        self,
        email: str,
        name: str,
        team: str,
        employee_id: str | None = None,
        gitlab_username: str | None = None,
    ) -> Employee:
        """
        Looks up an employee by email. 
        If found, updates their details (team, name, etc.).
        If not found, creates a new employee.
        This completely avoids `UNIQUE constraint failed: employees.email`.
        """
        emp = await self.get_by_email(email)
        
        if emp:
            # Update existing employee
            emp.name = name
            emp.team = team  # Will update to actual Excel team
            if employee_id:
                emp.employee_id = employee_id
            if gitlab_username:
                emp.gitlab_username = gitlab_username
        else:
            # Create new employee
            emp = Employee(
                email=email,
                name=name,
                team=team,
                employee_id=employee_id or "",
                gitlab_username=gitlab_username,
            )
            self._session.add(emp)
            
        await self._session.flush()
        return emp