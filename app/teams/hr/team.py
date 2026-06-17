from __future__ import annotations

from typing import Any, ClassVar

from langgraph.graph import StateGraph
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging_config import get_logger
from app.models.employee import Employee
from app.models.scores import (
    AttendanceScore,
    FinalScore,
    SentimentScore,
    WorkLogScore,
)
from app.repositories.employee_repository import EmployeeRepository
from app.repositories.score_repository import (
    AttendanceRepository,
    FinalScoreRepository,
    SentimentRepository,
    WorkLogRepository,
)
from app.shared.excel_parser.row_schema import CanonicalRow
from app.teams.base import TeamContext, TeamContract
from app.teams.hr.graph import run_hr_worker
from app.teams.hr.report import generate_hr_excel_report

logger = get_logger(__name__)


class HRTeam(TeamContract):
    team_key: ClassVar[str] = "hr"
    display_name: ClassVar[str] = "HR"
    aliases: ClassVar[frozenset[str]] = frozenset(
        {"hr", "hr_team"}
    )

    graph: ClassVar[StateGraph | None] = None

    async def run_per_employee(
        self,
        row: CanonicalRow,
        ctx: TeamContext,
    ) -> dict[str, Any]:
        run_id: int = ctx["run_id"]
        year: int = ctx["year"]
        month: int = ctx["month"]
        db: AsyncSession = ctx["db"]

        result: dict[str, Any] = {
            "employee_id": row.employee_id,
            "employee_email": row.employee_email,
            "final_score": 0.0,
            "segment_a_marks": 0.0,
            "segment_b_marks": 0.0,
            "base_total": 0.0,
            "reward_score": 0.0,
            "error": None,
        }

        log = logger.bind(employee_id=row.employee_id, run_id=run_id, year=year, month=month)
        log.info("hr_team_run_start")

        emp_repo = EmployeeRepository(db)
        employee: Employee | None = await emp_repo.get_by_employee_id(row.employee_id)
        if employee is None:
            msg = f"Employee {row.employee_id} not found in DB"
            log.error("employee_not_found")
            result["error"] = msg
            return result

        try:
            state = await run_hr_worker(
                employee_id=employee.employee_id,
                employee_email=employee.email,
                employee_name=employee.name,
                evaluation_run_id=run_id,
                year=year,
                month=month,
                db=db,
            )
        except Exception as exc:
            log.error("hr_worker_graph_failed", error=str(exc))
            result["error"] = str(exc)
            return result

        if state.get("fetch_error"):
            log.error("hr_worker_fetch_error", error=state["fetch_error"])
            result["error"] = state["fetch_error"]
            return result

        wl_repo = WorkLogRepository(db)
        await wl_repo.create(
            WorkLogScore(
                evaluation_run_id=run_id,
                employee_email=employee.email,
                total_log_hours=state["total_hours"],
                normalized_score=state["log_hours_score"],
                year=year,
                month=month,
            )
        )

        sent_repo = SentimentRepository(db)
        await sent_repo.create(
            SentimentScore(
                evaluation_run_id=run_id,
                employee_email=employee.email,
                score=state["sentiment_avg"],
                average_polarity=state["avg_polarity"],
                total_logs_analyzed=len(
                    [d for d in state.get("crm_description_records", [])
                     if str(d.get("employee_id", "")) == employee.employee_id]
                ),
                year=year,
                month=month,
            )
        )

        att_repo = AttendanceRepository(db)
        await att_repo.create(
            AttendanceScore(
                evaluation_run_id=run_id,
                employee_email=employee.email,
                present_days=state["attendance_present"],
                late_attendance=state["attendance_late"],
                work_days=state["attendance_work_days"],
                actual_work_days=state["attendance_work_days"],
                late_days=state["attendance_late"] // 3,
                score=state["attendance_score"],
                year=year,
                month=month,
            )
        )

        fs_repo = FinalScoreRepository(db)
        await fs_repo.create(
            FinalScore(
                evaluation_run_id=run_id,
                employee_email=employee.email,
                segment_a_marks=state["segment_a_marks"],
                attendance_score=state["attendance_score"],
                attendance_marks=state["attendance_marks"],
                problem_solving=state["tl_problem_solving"],
                kpi=state["tl_kpi"],
                general_assessment=state["tl_general"],
                tl_total=state["tl_total"],
                segment_b_marks=state["segment_b_marks"],
                base_total=state["base_total"],
                reward_score=0.0,
                final_score=state["final_score"],
                year=year,
                month=month,
            )
        )

        await db.flush()

        result.update({
            "final_score": state["final_score"],
            "segment_a_marks": state["segment_a_marks"],
            "segment_b_marks": state["segment_b_marks"],
            "base_total": state["base_total"],
            "reward_score": 0.0,
            "monthly_functional_score": state["monthly_functional_score"],
            "log_hours_score": state["log_hours_score"],
            "sentiment_score": state["sentiment_avg"],
            "total_log_hours": state["total_hours"],
            "attendance_score": state["attendance_score"],
            "problem_solving": state["tl_problem_solving"],
            "kpi": state["tl_kpi"],
            "general": state["tl_general"],
        })

        log.info("hr_team_run_done", final_score=state["final_score"])
        return result

    async def generate_report(
        self,
        run_id: int,
        emails: list[str],
        team_key: str,
        year: int,
        month: int,
        db: AsyncSession,
        **kwargs: Any,
    ) -> str | None:
        return await generate_hr_excel_report(
            run_id=run_id,
            emails=emails,
            team=team_key,
            year=year,
            month=month,
            db=db,
            col_names=kwargs.get("col_names"),
            team_display_name=kwargs.get("team_display_name", ""),
        )
