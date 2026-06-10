"""
app/teams/sqa/team.py
─────────────────────
SQATeam — LangGraph-backed implementation of TeamContract for "sqa".

Key differences from DeveloperTeam:
  - Segment A marks out of 30 (not 50)
  - No reward marks
  - Final score = (base_total / 80) * 100
"""

from __future__ import annotations

from typing import Any, ClassVar

from langgraph.graph import StateGraph
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging_config import get_logger
from app.models.employee import Employee
from app.models.scores import (
    AttendanceScore,
    CodeQualityScore,
    DeveloperFinalScore,
    FinalScore,
    SentimentScore,
    WorkLogScore,
)
from app.repositories.employee_repository import EmployeeRepository
from app.repositories.score_repository import (
    AttendanceRepository,
    CodeQualityRepository,
    FinalScoreRepository,
    SentimentRepository,
    WorkLogRepository,
)
from app.shared.excel_parser.row_schema import CanonicalRow
from app.teams.base import TeamContext, TeamContract
from app.teams.sqa.graph import run_sqa_worker
from app.teams.sqa.report import generate_sqa_reports

logger = get_logger(__name__)


class SQATeam(TeamContract):
    team_key: ClassVar[str] = "sqa"
    display_name: ClassVar[str] = "SQA"
    aliases: ClassVar[frozenset[str]] = frozenset({"sqa", "qa"})

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
        log.info("sqa_team_run_start")

        emp_repo = EmployeeRepository(db)
        employee: Employee | None = await emp_repo.get_by_employee_id(row.employee_id)
        if employee is None:
            msg = f"Employee {row.employee_id} not found in DB"
            log.error("employee_not_found")
            result["error"] = msg
            return result

        try:
            state = await run_sqa_worker(
                employee_id=employee.employee_id,
                employee_email=employee.email,
                employee_name=employee.name,
                gitlab_username=employee.gitlab_username or employee.employee_id,
                evaluation_run_id=run_id,
                year=year,
                month=month,
                db=db,
            )
        except Exception as exc:
            log.error("sqa_worker_graph_failed", error=str(exc))
            result["error"] = str(exc)
            return result

        if state.get("fetch_error"):
            log.error("sqa_worker_fetch_error", error=state["fetch_error"])
            result["error"] = state["fetch_error"]
            return result

        # 1. CodeQualityScore rows
        mr_scores: list[dict] = state.get("mr_scores", []) or []
        gitlab_username_used = employee.gitlab_username or employee.employee_id
        cq_repo = CodeQualityRepository(db)
        if mr_scores:
            for mr in mr_scores:
                await cq_repo.create(
                    CodeQualityScore(
                        evaluation_run_id=mr["evaluation_run_id"],
                        employee_email=mr["employee_email"],
                        mr_reference=mr["mr_reference"],
                        mr_title=mr["mr_title"],
                        raw_score=mr["raw_score"],
                        readability_score=mr["readability_score"],
                        logic_efficiency_score=mr["logic_efficiency_score"],
                        error_handling_score=mr["error_handling_score"],
                        architecture_score=mr["architecture_score"],
                        security_score=mr["security_score"],
                        reasoning=mr["reasoning"],
                        issues=mr["issues"],
                        model_used=mr["model_used"],
                        lines_added=mr.get("lines_added", 0),
                        lines_deleted=mr.get("lines_deleted", 0),
                    )
                )
        else:
            await cq_repo.create(
                CodeQualityScore(
                    evaluation_run_id=run_id,
                    employee_email=employee.email,
                    mr_reference="no_commits_found",
                    mr_title=f"No commits found for GitLab user '{gitlab_username_used}' in {year}-{month:02d}",
                    raw_score=0.0,
                    readability_score=0.0,
                    logic_efficiency_score=0.0,
                    error_handling_score=0.0,
                    architecture_score=0.0,
                    security_score=0.0,
                    reasoning=f"No GitLab commits were found for employee '{employee.name}' (email: {employee.email}, GitLab username used: '{gitlab_username_used}') during {year}-{month:02d}. No code quality score has been assigned.",
                    issues="[]",
                    model_used="no_commits",
                )
            )

        # 2. WorkLogScore
        wl_repo = WorkLogRepository(db)
        await wl_repo.create(
            WorkLogScore(
                evaluation_run_id=run_id,
                employee_email=employee.email,
                total_log_hours=state["total_hours"],
                normalized_score=state["work_log_score"],
                year=year,
                month=month,
            )
        )

        # 3. SentimentScore
        sent_repo = SentimentRepository(db)
        await sent_repo.create(
            SentimentScore(
                evaluation_run_id=run_id,
                employee_email=employee.email,
                score=state["sentiment_avg"],
                average_polarity=state["avg_polarity"],
                total_logs_analyzed=len(state.get("crm_description_records", [])),
                year=year,
                month=month,
            )
        )

        # 4. AttendanceScore
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

        # 5. FinalScore
        component2 = state["component2"]
        seg_a_raw = round((state["quality_check"] + component2) / 2, 4)
        fs_repo = FinalScoreRepository(db)
        await fs_repo.create(
            FinalScore(
                evaluation_run_id=run_id,
                employee_email=employee.email,
                quality_check_score=state["code_quality_ai"],
                resolution_rate=round(state["resolution_rate"], 4),
                reopen_quality_score=round(state["reopen_quality"], 4),
                lines_added_score=state["lines_added_score"],
                lines_deleted_score=state["lines_deleted_score"],
                component1_score=state["quality_check"],
                component2_score=component2,
                segment_a_score=seg_a_raw,
                segment_a_marks=state["segment_a_marks"],
                attendance_score=state["attendance_score"],
                attendance_marks=round(state["attendance_score"] / 10, 4),
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

        # 6. DeveloperFinalScore (reuse the same 24-col table for SQA)
        db.add(
            DeveloperFinalScore(
                evaluation_run_id=run_id,
                year=year,
                month=month,
                employee_id=employee.employee_id,
                employee_name=employee.name,
                employee_email=employee.email,
                code_quality_score=state["code_quality_ai"],
                resolution_rate=round(state["resolution_rate"], 4),
                reopen_quality_score=round(state["reopen_quality"], 4),
                lines_added_score=state["lines_added_score"],
                lines_deleted_score=state["lines_deleted_score"],
                component1_score=state["quality_check"],
                work_log_hours=state["total_hours"],
                work_log_score=state["work_log_score"],
                sentiment_score=state["sentiment_avg"],
                component2_score=component2,
                segment_a_score=seg_a_raw,
                segment_a_marks=state["segment_a_marks"],
                attendance_score=state["attendance_score"],
                attendance_marks=round(state["attendance_score"] / 10, 4),
                tl_problem_solving=state["tl_problem_solving"],
                tl_kpi=state["tl_kpi"],
                tl_general=state["tl_general"],
                tl_total=state["tl_total"],
                segment_b_marks=state["segment_b_marks"],
                base_total=state["base_total"],
                reward_score=0.0,
                final_score=state["final_score"],
            )
        )
        await db.flush()

        result.update({
            "final_score": state["final_score"],
            "segment_a_marks": state["segment_a_marks"],
            "segment_b_marks": state["segment_b_marks"],
            "base_total": state["base_total"],
            "reward_score": 0.0,
            "component1_score": state["quality_check"],
            "code_quality_ai": state["code_quality_ai"],
            "resolution_rate": round(state["resolution_rate"], 4),
            "reopen_quality_score": round(state["reopen_quality"], 4),
            "lines_added_score": state["lines_added_score"],
            "lines_deleted_score": state["lines_deleted_score"],
            "component2_score": component2,
            "work_log_hours": state["total_hours"],
            "work_log_score": state["work_log_score"],
            "sentiment_score": state["sentiment_avg"],
            "attendance_score": state["attendance_score"],
            "problem_solving": state["tl_problem_solving"],
            "kpi": state["tl_kpi"],
            "general": state["tl_general"],
        })

        log.info("sqa_team_run_done", final_score=state["final_score"])
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
        result = await generate_sqa_reports(
            run_id=run_id,
            emails=emails,
            year=year,
            month=month,
            db=db,
        )
        return result.get("code_quality_report") or result.get("final_report")
