"""
app/shared/persistence/tl_upserter.py
─────────────────────────────────────
Generic Employee + TLAssessmentScore upsert helper.

The TL Excel upload contains:
  - Employee identity (employee_id, email, name, gitlab_username, team)
  - TL marks (problem_solving | support_readiness, kpi, general)

Both the developer worker and the support worker persist the same two
rows per employee. Keeping the upsert logic here (not in any team) ensures
adding a new team doesn't duplicate the persistence code.

Behaviour (preserved byte-for-byte from legacy endpoints):
  1. Employee lookup by employee_id; if missing, by email; if still missing, create.
  2. Update mutable fields (name, email, team, gitlab_username) on existing rows.
  3. TLAssessmentScore upsert scoped to (run_id, email); for support teams
     support_readiness is stored in the legacy `problem_solving` column.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.employee import Employee
from app.models.scores import TLAssessmentScore
from app.repositories.employee_repository import EmployeeRepository
from app.repositories.score_repository import TLAssessmentRepository
from app.shared.excel_parser.row_schema import CanonicalRow


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

        Args:
            row: CanonicalRow parsed from the Excel.
            team_key: resolved team key (e.g. "developer", "impl_its").
            run_id: parent EvaluationRun id.
            year, month: evaluation period.
            use_support_readiness: if True, store support_readiness value in
                the TLAssessmentScore.problem_solving column (legacy mapping
                for support teams). If False, store problem_solving directly.

        Returns:
            The upserted Employee, or None if employee_id is empty.
        """
        emp = None
        if row.employee_id:
            emp = await self._emp_repo.get_by_employee_id(row.employee_id)
        if emp is None:
            emp = await self._emp_repo.get_by_email(row.employee_email)
        if emp is None:
            # Use email as id if no employee_id supplied (fallback behaviour)
            emp_id_to_use = row.employee_id or row.employee_email
            emp = await self._emp_repo.create(
                Employee(
                    employee_id=emp_id_to_use,
                    name=row.employee_name or row.employee_email,
                    email=row.employee_email,
                    team=team_key,
                    gitlab_username=row.gitlab_username,
                    is_active=True,
                )
            )
        else:
            emp.team = team_key
            if row.employee_id and emp.employee_id != row.employee_id:
                emp.employee_id = row.employee_id
            if row.employee_name and (not emp.name or emp.name == emp.email):
                emp.name = row.employee_name
            if row.gitlab_username:
                emp.gitlab_username = row.gitlab_username

        # Upsert TLAssessmentScore
        if use_support_readiness:
            ps_value = row.support_readiness
        else:
            ps_value = row.problem_solving

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
