"""
app/shared/persistence/tl_upserter.py
─────────────────────────────────────
Generic Employee + TLAssessmentScore upsert helper.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging_config import get_logger
from app.models.employee import Employee
from app.models.scores import TLAssessmentScore
from app.repositories.employee_repository import EmployeeRepository
from app.repositories.score_repository import TLAssessmentRepository
from app.shared.excel_parser.row_schema import CanonicalRow

logger = get_logger(__name__)


class TLUpserter:
    """
    Upserts Employee + TLAssessmentScore rows for one Excel upload.
    """

    def __init__(self, db: AsyncSession) -> None:
        self._db = db
        self._emp_repo = EmployeeRepository(db)
        self._tl_repo = TLAssessmentRepository(db)

    async def upsert_employee_and_tl(
        self,
        row: CanonicalRow,
        team_key: str,
        run_id: int,
        year: int,
        month: int,
        *,
        use_support_readiness: bool = False,
    ) -> Employee | None:
        """
        Upsert one employee + their TL assessment row.
        """
        # 1. Resolve Team Name: Priority to Excel (row.team_name), Fallback to team_key
        actual_team_to_save = row.team_name.strip() if (row.team_name and row.team_name.strip()) else team_key

        # 2. Lookup by email
        emp = await self._emp_repo.get_by_email(row.employee_email)

        # 3. Fallback: lookup by employee_id
        if emp is None and row.employee_id:
            emp = await self._emp_repo.get_by_employee_id(row.employee_id)

        if emp is None:
            emp = await self._emp_repo.create(
                Employee(
                    employee_id=row.employee_id or row.employee_email,
                    name=row.employee_name or row.employee_email,
                    email=row.employee_email.lower(),
                    team=actual_team_to_save,  # Save the Excel team name
                    gitlab_username=row.gitlab_username,
                    is_active=True,
                )
            )
        else:
            # Update team to the one from Excel
            emp.team = actual_team_to_save

            # Reconcile employee_id
            if row.employee_id and emp.employee_id != row.employee_id:
                existing_by_id = await self._emp_repo.get_by_employee_id(row.employee_id)
                if existing_by_id and existing_by_id.id != emp.id:
                    logger.warning(
                        "employee_id_reassignment_skipped",
                        current=emp.employee_id,
                        requested=row.employee_id,
                        email=row.employee_email,
                    )
                else:
                    emp.employee_id = row.employee_id

            if row.employee_name:
                emp.name = row.employee_name
            if row.gitlab_username:
                emp.gitlab_username = row.gitlab_username

            canonical_email = row.employee_email.strip().lower()
            if emp.email.lower() != canonical_email:
                emp.email = canonical_email

        # 4. Upsert TLAssessmentScore
        ps_value = row.support_readiness if use_support_readiness else row.problem_solving
        total = round(ps_value + row.kpi + row.general, 4)

        existing_tl = await self._tl_repo.get_by_run_and_email(
            run_id=run_id, email=row.employee_email
        )
        if existing_tl is None:
            await self._tl_repo.create(
                TLAssessmentScore(
                    evaluation_run_id=run_id,
                    employee_email=row.employee_email,
                    year=year,
                    month=month,
                    problem_solving=ps_value,
                    kpi=row.kpi,
                    general=row.general,
                    total=total,
                    uploaded_by="api",
                )
            )
        else:
            existing_tl.problem_solving = ps_value
            existing_tl.kpi = row.kpi
            existing_tl.general = row.general
            existing_tl.total = total

        return emp