from __future__ import annotations

import datetime
from decimal import Decimal

from sqlalchemy.dialects.mysql import insert

from app.core.logging_config import get_logger
from app.core.mysql_db import MySQLSessionFactory
from app.models.performance_summary import EmployeePerformanceSummary

logger = get_logger(__name__)


async def upsert_performance_summary(
    *,
    emp_email: str,
    emp_name: str = "",
    team_name: str = "",
    year: int,
    month: int,
    financial_contribution: float | None = None,
    functional_job: float | None = None,
    critical_thinking_and_problem_solving: float | None = None,
    office_discipline: float | None = None,
    performance_agreement: float | None = None,
    team_lead_assessment: float | None = None,
    consolidated_score: float | None = None,
) -> None:
    eval_date = datetime.date(int(year), int(month), 1)

    values = {
        "emp_email": emp_email,
        "emp_name": emp_name,
        "team_name": team_name,
        "date": eval_date,
        "financial_contribution": (
            round(Decimal(str(financial_contribution)), 2)
            if financial_contribution is not None
            else Decimal("0.00")
        ),
        "functional_job": (
            round(Decimal(str(functional_job)), 2)
            if functional_job is not None
            else None
        ),
        "critical_thinking_and_problem_solving": (
            int(round(critical_thinking_and_problem_solving))
            if critical_thinking_and_problem_solving is not None
            else None
        ),
        "office_discipline": (
            int(round(office_discipline))
            if office_discipline is not None
            else None
        ),
        "performance_agreement": (
            int(round(performance_agreement))
            if performance_agreement is not None
            else None
        ),
        "team_lead_assessment": (
            int(round(team_lead_assessment))
            if team_lead_assessment is not None
            else None
        ),
        "consolidated_score": (
            round(Decimal(str(consolidated_score)), 2)
            if consolidated_score is not None
            else None
        ),
    }

    stmt = insert(EmployeePerformanceSummary).values(**values)
    stmt = stmt.on_duplicate_key_update(**values)

    async with MySQLSessionFactory() as mysql_db:
        try:
            await mysql_db.execute(stmt)
            await mysql_db.commit()
            logger.info(
                "performance_summary_upserted",
                emp_email=emp_email,
                date=str(eval_date),
                team=team_name,
            )
        except Exception:
            await mysql_db.rollback()
            logger.error(
                "performance_summary_upsert_failed",
                emp_email=emp_email,
                date=str(eval_date),
            )
            raise
