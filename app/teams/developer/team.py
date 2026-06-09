"""
app/teams/developer/team.py
───────────────────────────
DeveloperTeam — the LangGraph-backed implementation of the
TeamContract for the "developer" team.

This class is the only place that talks to the DB. The graph
(app/teams/developer/graph.py) is **pure** — it only computes scores.
``run_per_employee`` then writes them in the EXACT order used by the
legacy ``DeveloperScorer.calculate()`` to guarantee 100 % functional
parity (same rows, same column values, same row-write sequence).

Legacy DB write order (preserved 1-for-1):
    1. CodeQualityScore  (one row per MR bundle, or one "no_commits" sentinel)
    2. WorkLogScore      (one row)
    3. SentimentScore    (one row)
    4. AttendanceScore   (one row)
    5. FinalScore        (one row)  → flushed
    6. DeveloperFinalScore (one row) → flushed
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
from app.teams.developer.graph import run_developer_worker
from app.teams.developer.report import generate_developer_reports

logger = get_logger(__name__)


class DeveloperTeam(TeamContract):
    """
    Developer worker team.

    Delegates per-employee scoring to a LangGraph (graph.py) and then
    persists the computed scores via the standard SQLAlchemy repositories.
    """

    # ── Class-level configuration ─────────────────────────────────────────────
    team_key: ClassVar[str] = "developer"
    display_name: ClassVar[str] = "Developer"
    aliases: ClassVar[frozenset[str]] = frozenset({"developer", "dev"})

    # The compiled graph lives in app/teams/developer/graph.py and is
    # invoked inside run_per_employee. We mark the attribute here so the
    # base-class type-check is satisfied and discovery tools can inspect
    # which teams expose a graph.
    graph: ClassVar[StateGraph | None] = None  # actual graph is module-level in graph.py

    # ── Per-employee scoring ──────────────────────────────────────────────────

    async def run_per_employee(
        self,
        row: CanonicalRow,
        ctx: TeamContext,
    ) -> dict[str, Any]:
        """
        Score a single developer employee and persist the result.

        Mirrors the legacy DeveloperScorer.calculate() body, including
        the exact row-write order:
            CodeQuality → WorkLog → Sentiment → Attendance →
            FinalScore → DeveloperFinalScore
        """
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

        log = logger.bind(
            employee_id=row.employee_id,
            run_id=run_id,
            year=year,
            month=month,
        )
        log.info("developer_team_run_start")

        # ── Look up the canonical Employee record ────────────────────────────
        emp_repo = EmployeeRepository(db)
        employee: Employee | None = await emp_repo.get_by_employee_id(row.employee_id)
        if employee is None:
            msg = f"Employee {row.employee_id} not found in DB"
            log.error("employee_not_found")
            result["error"] = msg
            return result

        # ── Run the pure worker graph (computes all scores) ──────────────────
        try:
            state = await run_developer_worker(
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
            log.error("developer_worker_graph_failed", error=str(exc))
            result["error"] = str(exc)
            return result

        # If the graph hit a fatal fetch error, mirror legacy behaviour
        # (no DB writes, propagate the error).
        if state.get("fetch_error"):
            log.error("developer_worker_fetch_error", error=state["fetch_error"])
            result["error"] = state["fetch_error"]
            return result

        # ── Persist in legacy order ──────────────────────────────────────────

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
                    mr_title=(
                        f"No commits found for GitLab user '{gitlab_username_used}' "
                        f"in {year}-{month:02d}"
                    ),
                    raw_score=0.0,
                    readability_score=0.0,
                    logic_efficiency_score=0.0,
                    error_handling_score=0.0,
                    architecture_score=0.0,
                    security_score=0.0,
                    reasoning=(
                        f"No GitLab commits were found for employee '{employee.name}' "
                        f"(email: {employee.email}, GitLab username used: "
                        f"'{gitlab_username_used}') during {year}-{month:02d}. "
                        "No code quality score has been assigned."
                    ),
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

        # 5. FinalScore (matches legacy round values)
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
                reward_score=state["reward"],
                final_score=state["final_score"],
                year=year,
                month=month,
            )
        )

        # Flush so FinalScore gets its rowid before DeveloperFinalScore is
        # inserted (matches legacy behaviour).
        await db.flush()

        # 6. DeveloperFinalScore (24-col report twin)
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
                reward_score=state["reward"],
                final_score=state["final_score"],
            )
        )
        await db.flush()

        # ── Build result dict (mirrors legacy DeveloperScorer.calculate result) ─
        result.update(
            {
                "final_score": state["final_score"],
                "segment_a_marks": state["segment_a_marks"],
                "segment_b_marks": state["segment_b_marks"],
                "base_total": state["base_total"],
                "reward_score": state["reward"],
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
            }
        )

        log.info("developer_team_run_done", final_score=state["final_score"])
        return result

    # ── Team-level Excel report ───────────────────────────────────────────────

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
        """Generate the developer Excel report and move to outputs/reports/."""
        result = await generate_developer_reports(
            run_id=run_id,
            emails=emails,
            year=year,
            month=month,
            db=db,
        )
        # The supervisor expects the primary (code-quality) report path.
        return result.get("code_quality_report") or result.get("final_report")
