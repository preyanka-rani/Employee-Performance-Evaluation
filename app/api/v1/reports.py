"""
app/api/v1/reports.py
──────────────────────
Report endpoints for individual employees and team summaries.

GET /api/v1/reports/{employee_id}/{year}/{month}  – Individual score breakdown
GET /api/v1/reports/team/{team}/{year}/{month}     – All employees in a team
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.repositories.employee_repository import EmployeeRepository
from app.repositories.score_repository import FinalScoreRepository
from app.schemas.reports import (
    EmployeeReportResponse,
    TeamReportEntry,
    TeamReportResponse,
)

router = APIRouter(prefix="/reports", tags=["reports"])


@router.get("/{employee_id}/{year}/{month}", response_model=EmployeeReportResponse)
async def get_employee_report(
    employee_id: str,
    year: int,
    month: int,
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(get_current_user),
) -> EmployeeReportResponse:
    """Return the full score breakdown for a single employee in a given period."""
    emp_repo = EmployeeRepository(db)
    employee = await emp_repo.get_by_employee_id(employee_id)
    if employee is None:
        raise HTTPException(status_code=404, detail="Employee not found.")

    fs_repo = FinalScoreRepository(db)
    score = await fs_repo.get_by_email_and_period(
        email=employee.email,
        year=year,
        month=month,
    )
    if score is None:
        raise HTTPException(
            status_code=404,
            detail=f"No evaluation found for employee {employee_id} in {year}-{month}.",
        )

    return EmployeeReportResponse(
        employee_id=employee.employee_id,
        name=employee.name,
        email=employee.email,
        team=employee.team,
        year=year,
        month=month,
        quality_check_score=score.quality_check_score,
        work_log_score=score.work_log_score,
        sentiment_score=score.sentiment_score,
        attendance_score=score.attendance_score,
        problem_solving_score=score.problem_solving_score,
        kpi_score=score.kpi_score,
        general_score=score.general_score,
        segment_a_marks=score.segment_a_marks,
        segment_b_marks=score.segment_b_marks,
        base_total=score.base_total,
        reward_score=score.reward_score,
        final_score=score.final_score,
    )


@router.get("/team/{team}/{year}/{month}", response_model=TeamReportResponse)
async def get_team_report(
    team: str,
    year: int,
    month: int,
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(get_current_user),
) -> TeamReportResponse:
    """Return aggregated scores for all evaluated employees in a team."""
    emp_repo = EmployeeRepository(db)
    employees = await emp_repo.get_by_team(team)
    if not employees:
        raise HTTPException(
            status_code=404, detail=f"No employees found in team '{team}'."
        )

    emails = [e.email for e in employees]
    email_to_emp = {e.email: e for e in employees}

    fs_repo = FinalScoreRepository(db)
    scores = await fs_repo.get_team_scores_by_period(
        emails=emails,
        year=year,
        month=month,
    )

    entries: list[TeamReportEntry] = []
    for score in scores:
        emp = email_to_emp.get(score.employee_email)
        entries.append(
            TeamReportEntry(
                employee_id=emp.employee_id if emp else "unknown",
                name=emp.name if emp else "unknown",
                email=score.employee_email,
                final_score=score.final_score,
                segment_a_marks=score.segment_a_marks,
                segment_b_marks=score.segment_b_marks,
                base_total=score.base_total,
                reward_score=score.reward_score,
            )
        )

    # Sort by final_score descending
    entries.sort(key=lambda e: e.final_score, reverse=True)

    team_avg = (
        round(sum(e.final_score for e in entries) / len(entries), 2) if entries else 0.0
    )

    return TeamReportResponse(
        team=team,
        year=year,
        month=month,
        employee_count=len(entries),
        team_average_score=team_avg,
        entries=entries,
    )
