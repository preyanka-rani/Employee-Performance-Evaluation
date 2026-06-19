from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Date, DateTime, Integer, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class EmployeePerformanceSummary(Base):
    __tablename__ = "employee_performance_summary"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    emp_email: Mapped[str] = mapped_column(String(150), nullable=False)
    emp_name: Mapped[str] = mapped_column(String(150), nullable=False)
    team_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    date: Mapped[date | None] = mapped_column(Date, nullable=True)
    financial_contribution: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    functional_job: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    critical_thinking_and_problem_solving: Mapped[int | None] = mapped_column(Integer, nullable=True)
    office_discipline: Mapped[int | None] = mapped_column(Integer, nullable=True)
    performance_agreement: Mapped[int | None] = mapped_column(Integer, nullable=True)
    team_lead_assessment: Mapped[int | None] = mapped_column(Integer, nullable=True)
    consolidated_score: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("emp_email", "date", name="unique_emp_month"),
    )

    def __repr__(self) -> str:
        return (
            f"<EmployeePerformanceSummary(emp_email={self.emp_email!r}, "
            f"date={self.date!r}, consolidated_score={self.consolidated_score!r})>"
        )
