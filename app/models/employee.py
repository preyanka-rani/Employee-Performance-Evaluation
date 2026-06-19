"""
app/models/employee.py
───────────────────────
Employee master record stored in the internal SQLite database.
This is the canonical identity used across all evaluation calculations.
"""

from datetime import UTC, datetime

from sqlalchemy import DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class Employee(Base):
    """
    Represents a single employee who participates in performance evaluations.

    Fields:
        employee_id   – HR identifier (matches employee_id in MySQL source DBs).
        gitlab_username – GitLab username used to filter MRs.
        team          – Team membership (e.g. 'developer', 'sqa', 'business').
    """

    __tablename__ = "employees"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    employee_id: Mapped[str] = mapped_column(
        String(20), unique=True, nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    email: Mapped[str] = mapped_column(
        String(255), unique=True, nullable=False, index=True
    )
    team: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    gitlab_username: Mapped[str | None] = mapped_column(String(100), nullable=True)
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=lambda: datetime.now(UTC),
        nullable=False,
    )

    def __repr__(self) -> str:
        return f"<Employee id={self.employee_id} name={self.name!r} team={self.team!r}>"
