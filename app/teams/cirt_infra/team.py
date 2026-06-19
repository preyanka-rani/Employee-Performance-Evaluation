"""
app/teams/cirt_infra/team.py
────────────────────────────
CIRTInfraTeam — the LangGraph-backed implementation of the
TeamContract for the ``"cirt_infra"`` team.

This class is the only place that talks to the DB. The graph
(``app/teams/cirt_infra/graph.py``) is **pure** — it only computes scores.
``run_per_employee`` then writes them in the same order as the legacy
``cirt_infra_evaluation.ipynb`` reference:

    1. WorkLogScore      (one row)
    2. SentimentScore    (one row)
    3. AttendanceScore   (one row)
    4. FinalScore        (one row)  → flushed

The shared ``FinalScore`` table is reused (mirrors the Support pattern).
Developer-specific columns on the shared table are written as 0.
"""

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
from app.shared.persistence.summary_upserter import upsert_performance_summary
from app.teams.base import TeamContext, TeamContract
from app.teams.cirt_infra.graph import run_cirt_infra_worker
from app.teams.cirt_infra.report import generate_cirt_infra_excel_report

logger = get_logger(__name__)


class CIRTInfraTeam(TeamContract):
    """
    CIRT & Infra worker team.

    Delegates per-employee scoring to a LangGraph (graph.py) and then
    persists the computed scores via the standard SQLAlchemy repositories.
    """

    # ── Class-level configuration ─────────────────────────────────────────────
    team_key: ClassVar[str] = "cirt_infra"
    display_name: ClassVar[str] = "CIRT & Infra"
    aliases: ClassVar[frozenset[str]] = frozenset(
        {"cirt_infra", "cirt_infra_team", "cirt", "infra"}
    )

    # The compiled graph lives in app/teams/cirt_infra/graph.py and is
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
        Score a single CIRT & Infra employee and persist the result.

        Persists, in legacy order:
            WorkLogScore → SentimentScore → AttendanceScore → FinalScore
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
            "reward_score": 0.0,  # CIRT & Infra has no reward marks
            "error": None,
        }

        log = logger.bind(
            employee_id=row.employee_id,
            run_id=run_id,
            year=year,
            month=month,
        )
        log.info("cirt_infra_team_run_start")

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
            state = await run_cirt_infra_worker(
                employee_id=employee.employee_id,
                employee_email=employee.email,
                employee_name=employee.name,
                evaluation_run_id=run_id,
                year=year,
                month=month,
                db=db,
            )
        except Exception as exc:
            log.error("cirt_infra_worker_graph_failed", error=str(exc))
            result["error"] = str(exc)
            return result

        # If the graph hit a fatal fetch error, mirror legacy behaviour
        # (no DB writes, propagate the error).
        if state.get("fetch_error"):
            log.error("cirt_infra_worker_fetch_error", error=state["fetch_error"])
            result["error"] = state["fetch_error"]
            return result

        # ── Persist in legacy order ──────────────────────────────────────────

        # 1. WorkLogScore
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

        # 2. SentimentScore
        sent_repo = SentimentRepository(db)
        await sent_repo.create(
            SentimentScore(
                evaluation_run_id=run_id,
                employee_email=employee.email,
                score=state["sentiment_avg"],
                average_polarity=state["avg_polarity"],
                total_logs_analyzed=len(
                    [
                        d
                        for d in state.get("crm_description_records", [])
                        if str(d.get("employee_id", "")) == employee.employee_id
                    ]
                ),
                year=year,
                month=month,
            )
        )

        # 3. AttendanceScore
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

        # 4. FinalScore (reuses the shared table; CIRT-specific columns only)
        fs_repo = FinalScoreRepository(db)
        await fs_repo.create(
            FinalScore(
                evaluation_run_id=run_id,
                employee_email=employee.email,
                # Segment A — CIRT
                segment_a_marks=state["segment_a_marks"],
                # Segment B
                attendance_score=state["attendance_score"],
                attendance_marks=state["attendance_marks"],
                # TL Assessment (problem_solving column carries support_readiness)
                problem_solving=state["tl_support_readiness"],
                kpi=state["tl_kpi"],
                general_assessment=state["tl_general"],
                tl_total=state["tl_total"],
                segment_b_marks=state["segment_b_marks"],
                # Final computation
                base_total=state["base_total"],
                reward_score=0.0,  # CIRT & Infra: no reward
                final_score=state["final_score"],
                year=year,
                month=month,
            )
        )

        # Flush so FinalScore gets its rowid before any subsequent reads
        # (matches the developer/team pattern).
        await db.flush()

        await upsert_performance_summary(
            emp_email=employee.email,
            emp_name=employee.name,
            team_name=self.team_key,
            year=year,
            month=month,
            financial_contribution=0.0,
            functional_job=state["segment_a_marks"],
            critical_thinking_and_problem_solving=state["tl_support_readiness"],
            office_discipline=state["attendance_marks"],
            performance_agreement=state["tl_kpi"],
            team_lead_assessment=state["tl_general"],
            consolidated_score=state["final_score"],
        )

        # ── Build result dict (mirrors legacy CIRT evaluation result) ────────
        result.update(
            {
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
                "support_readiness": state["tl_support_readiness"],
                "kpi": state["tl_kpi"],
                "general": state["tl_general"],
            }
        )

        log.info("cirt_infra_team_run_done", final_score=state["final_score"])
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
        """
        Generate the CIRT & Infra Excel report and save it to
        ``outputs/cirt_infra/CIRT_Infra_Final_Report_{team_key}_{year}_{month:02d}.xlsx``.
        """
        return await generate_cirt_infra_excel_report(
            run_id=run_id,
            emails=emails,
            team=team_key,
            year=year,
            month=month,
            db=db,
            col_names=kwargs.get("col_names"),
            team_display_name=kwargs.get("team_display_name", ""),
        )
