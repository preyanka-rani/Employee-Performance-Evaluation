"""
app/services/support_teams/scoring/support_scorer.py
──────────────────────────────────────────────────────
Concrete scorer for Support Teams (Impl & ITS, Onsite Support, Production, Tech Support).

Inherits from AbstractScorer and delegates to the LangGraph evaluation workflow.
TL assessment scores are loaded from the TLAssessmentScore table (stored by
the bulk-run API endpoint before calling this scorer).

TL column mapping:
    TLAssessmentScore.problem_solving → support_readiness (same 0-10 range)
    TLAssessmentScore.kpi             → kpi
    TLAssessmentScore.general         → general
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging_config import get_logger
from app.models.employee import Employee
from app.repositories.score_repository import TLAssessmentRepository
from app.services.scoring.base import AbstractScorer
from app.services.support_teams.workflows.support_evaluation_workflow import (
    run_support_evaluation,
)

logger = get_logger(__name__)


class SupportTeamScorer(AbstractScorer):
    """
    Concrete scorer for all support sub-teams:
        - Impl & ITS     (team key: "impl_its")
        - Onsite Support (team key: "onsite_support")
        - Production     (team key: "production")
        - Tech Support   (team key: "tech_support")

    All four teams share identical scoring logic. The team key is passed
    through to the workflow for logging/audit purposes only.

    Data flow per employee:
        1. Load TL marks from TLAssessmentScore table (uploaded via Excel)
        2. Run LangGraph workflow (fetches MySQL data, computes all scores)
        3. Workflow persists SupportCRMLogScore, SupportTicketScore, SupportFinalScore
        4. Return standardised result dict to the API caller
    """

    async def calculate(
        self,
        employee: Employee,
        evaluation_run_id: int,
        year: int,
        month: int,
        db: AsyncSession,
        *,
        prefetched_crm_log_records: list | None = None,
        prefetched_ticket_records: list | None = None,
        prefetched_attendance_records: list | None = None,
    ) -> dict:
        """Evaluate one support-team employee.

        When the ``prefetched_*`` arguments are supplied (team-wide batch data
        pre-fetched before the scoring loop) the LangGraph workflow will skip
        all MySQL calls and use the supplied records directly, keyed by email.
        This reduces MySQL load from N×3 queries per run to 3 queries total.
        """
        log = logger.bind(
            employee_email=employee.email,
            employee_id=employee.employee_id,
            year=year,
            month=month,
            team=employee.team,
        )
        log.info("support_scorer_start")

        result: dict = {
            "employee_id": employee.employee_id,
            "employee_email": employee.email,
            "final_score": 0.0,
            "segment_a_marks": 0.0,
            "segment_b_marks": 0.0,
            "base_total": 0.0,
            "reward_score": 0.0,  # Always 0 — support teams have no reward marks
            "error": None,
        }

        # ── 1. Load TL assessment from DB ─────────────────────────────────────
        tl_repo = TLAssessmentRepository(db)
        tl_score = await tl_repo.get_for_employee_period(
            employee_email=employee.email,
            year=year,
            month=month,
            evaluation_run_id=evaluation_run_id,
        )

        if tl_score is None:
            log.warning("support_tl_score_missing", email=employee.email)
            result["error"] = (
                f"TL assessment not found for {employee.email} ({year}/{month:02d}). "
                "Upload a TL Excel first."
            )
            return result

        # For support teams: TLAssessmentScore.problem_solving → support_readiness
        tl_support_readiness: float = float(tl_score.problem_solving or 0.0)
        tl_kpi: float = float(tl_score.kpi or 0.0)
        tl_general: float = float(tl_score.general or 0.0)

        log.info(
            "support_tl_loaded",
            support_readiness=tl_support_readiness,
            kpi=tl_kpi,
            general=tl_general,
        )

        # ── 2. Run LangGraph evaluation workflow ──────────────────────────────
        try:
            final_state = await run_support_evaluation(
                employee_email=employee.email,
                employee_id=str(employee.employee_id),
                evaluation_run_id=evaluation_run_id,
                year=year,
                month=month,
                team=employee.team or "support",
                tl_support_readiness=tl_support_readiness,
                tl_kpi=tl_kpi,
                tl_general=tl_general,
                db=db,
                prefetched_crm_log_records=prefetched_crm_log_records,
                prefetched_ticket_records=prefetched_ticket_records,
                prefetched_attendance_records=prefetched_attendance_records,
            )
        except Exception as exc:
            log.error("support_workflow_failed", error=str(exc))
            result["error"] = f"Evaluation workflow failed: {exc}"
            return result

        # ── 3. Build result dict ──────────────────────────────────────────────
        workflow_error = final_state.get("workflow_error")
        persist_error = final_state.get("persist_error")

        error_msg: str | None = None
        if workflow_error:
            error_msg = f"Workflow error: {workflow_error}"
        elif persist_error:
            error_msg = f"Persist error: {persist_error}"

        result.update(
            {
                "final_score": final_state.get("final_score", 0.0),
                "segment_a_marks": final_state.get("segment_a_marks", 0.0),
                "segment_b_marks": final_state.get("segment_b_marks", 0.0),
                "base_total": final_state.get("base_total", 0.0),
                "reward_score": 0.0,
                # CRM Log
                "total_log_hours": final_state.get("total_log_hours", 0.0),
                "log_hours_score": final_state.get("log_hours_score", 0.0),
                "sentiment_score": final_state.get("sentiment_score", 0.0),
                "crm_log_score": final_state.get("crm_log_score", 0.0),
                # Tickets
                "total_tickets": final_state.get("total_tickets", 0),
                "average_taken_days": final_state.get("average_taken_days", 0.0),
                "tickets_evaluation_score": final_state.get(
                    "tickets_evaluation_score", 0.0
                ),
                # Segment A
                "monthly_functional_score": final_state.get(
                    "monthly_functional_score", 0.0
                ),
                # Segment B
                "attendance_score": final_state.get("attendance_score", 0.0),
                "attendance_marks": final_state.get("attendance_marks", 0.0),
                "tl_support_readiness": tl_support_readiness,
                "tl_kpi": tl_kpi,
                "tl_general": tl_general,
                "tl_total": final_state.get("tl_total", 0.0),
                "error": error_msg,
            }
        )

        log.info(
            "support_scorer_done",
            final_score=result["final_score"],
            segment_a=result["segment_a_marks"],
            segment_b=result["segment_b_marks"],
            base_total=result["base_total"],
        )

        return result
