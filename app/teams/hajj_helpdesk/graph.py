"""
app/teams/hajj_helpdesk/graph.py
────────────────────────────────
LangGraph StateGraph workflow for Hajj Helpdesk Team evaluation.

This module is fully self-contained: the workflow state TypedDict and the
node implementations live together because they are tightly coupled.

Pipeline:

    START
      ↓
    fetch_data_node       — Parallel fetch: CRM logs, tickets, attendance
      ↓
    compute_crm_score_node — Normalise log hours + sentiment → crm_log_score
      ↓
    compute_tickets_node   — Volume + speed tiers → tickets_evaluation_score
      ↓
    compute_functional_node — CRM*0.8 + Tickets*0.2 → monthly_functional_score
                             → segment_a_marks (scaled to 30)
      ↓
    compute_segment_b_node  — Attendance/10 + TL marks → segment_b_marks (max 50)
      ↓
    finalize_score_node     — base_total + final_score (no reward marks)
      ↓
    persist_results_node    — Write CRMLogScore, TicketScore, FinalScore to SQLite
      ↓
    END

No LLM/AI calls are needed — sentiment uses TextBlob locally.

NOTE: TL assessment scores (support_readiness, kpi, general) must be loaded
into state BEFORE calling run_hajj_helpdesk_evaluation() — they come from the
TL Excel upload stored in the DB, not from MySQL.
"""

from __future__ import annotations

import asyncio
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging_config import get_logger
from app.models.support_scores import (
    SupportCRMLogScore,
    SupportFinalScore,
    SupportTicketScore,
)
from app.services.ai.sentiment import compute_employee_sentiment_score
from app.shared.data_sources.mysql_client import MySQLHRClient
from app.shared.data_sources.support_crm_client import SupportCRMClient
from app.shared.data_sources.support_tickets_client import SupportTicketsClient
from app.teams.hajj_helpdesk.formulas import (
    compute_attendance_marks,
    compute_crm_log_score,
    compute_functional_score,
    compute_hajj_helpdesk_final_score,
    compute_segment_a_marks,
    compute_segment_b_marks,
    compute_tickets_evaluation_score,
    compute_tl_total,
)

logger = get_logger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# State TypedDict
# ─────────────────────────────────────────────────────────────────────────────


class HajjHelpdeskEvalState(TypedDict):
    """
    Shared state object passed between all workflow nodes.

    Populated incrementally as the workflow progresses:
      fetch_crm_logs_node      → crm_log_records, crm_fetch_error
      fetch_tickets_node       → ticket_records, tickets_fetch_error
      fetch_attendance_node    → attendance_records, attendance_fetch_error
      compute_crm_score_node   → crm_log_score, log_hours_score, sentiment_score, total_log_hours
      compute_tickets_node     → tickets_evaluation_score, total_tickets, avg_taken_days
      compute_functional_node  → monthly_functional_score, segment_a_marks
      compute_segment_b_node   → attendance_marks, tl_marks, segment_b_marks
      finalize_node            → base_total, final_score
      persist_node             → persisted (bool), persist_error
    """

    # ── Inputs ────────────────────────────────────────────────────────────────
    employee_email: str
    employee_id: str
    evaluation_run_id: int
    year: int
    month: int
    team: str

    # TL assessment scores (loaded from DB before workflow runs)
    tl_support_readiness: float  # 0-10
    tl_kpi: float  # 0-15
    tl_general: float  # 0-15

    # ── Raw fetched data ──────────────────────────────────────────────────────
    crm_log_records: list[
        dict[str, Any]
    ]  # [{employee_id, user_email, log_hour, description}]
    ticket_records: list[
        dict[str, Any]
    ]  # [{user_email, total_tickets, average_taken_days}]
    attendance_records: list[dict[str, Any]]  # [{user_email, attendance_score}]

    # ── Fetch errors (None means success) ────────────────────────────────────
    crm_fetch_error: str | None
    tickets_fetch_error: str | None
    attendance_fetch_error: str | None

    # ── Computed CRM log scores ───────────────────────────────────────────────
    total_log_hours: float
    log_hours_score: float  # tiered normalised 0-100
    sentiment_score: float  # average TextBlob sentiment 0-100
    average_polarity: float
    crm_log_score: float  # log_hours*0.9 + sentiment*0.1 (0-100)

    # ── Computed ticket scores ────────────────────────────────────────────────
    total_tickets: int
    average_taken_days: float
    monthly_tickets_score: float  # volume-based 0-100
    monthly_ticket_resolved_score: float  # speed-based 0-100
    tickets_evaluation_score: float  # 0.7*volume + 0.3*speed (0-100)

    # ── Segment A ─────────────────────────────────────────────────────────────
    monthly_functional_score: float  # crm*0.8 + tickets*0.2 (0-100)
    segment_a_marks: float  # functional_score * 0.3 (0-30)

    # ── Segment B ─────────────────────────────────────────────────────────────
    attendance_score: float  # raw attendance 0-100
    attendance_marks: float  # attendance_score / 10 (0-10)
    tl_total: float  # sum of TL marks (0-40)
    segment_b_marks: float  # attendance_marks + tl_total (0-50)

    # ── Final ─────────────────────────────────────────────────────────────────
    base_total: float  # segment_a + segment_b (0-80)
    final_score: float  # (base_total / 80) * 100 (0-100)

    # ── Workflow metadata ─────────────────────────────────────────────────────
    persisted: bool
    persist_error: str | None
    workflow_error: str | None  # fatal error that stopped the workflow

    # ── Batch-fetch optimisation ──────────────────────────────────────────────
    data_prefetched: bool


# ── Helpers ───────────────────────────────────────────────────────────────────


def _default_state(
    employee_email: str,
    employee_id: str,
    evaluation_run_id: int,
    year: int,
    month: int,
    team: str,
    tl_support_readiness: float,
    tl_kpi: float,
    tl_general: float,
    *,
    crm_log_records: list | None = None,
    ticket_records: list | None = None,
    attendance_records: list | None = None,
    data_prefetched: bool = False,
) -> HajjHelpdeskEvalState:
    """Return a fully-initialised default state."""
    return HajjHelpdeskEvalState(
        # Inputs
        employee_email=employee_email,
        employee_id=employee_id,
        evaluation_run_id=evaluation_run_id,
        year=year,
        month=month,
        team=team,
        tl_support_readiness=tl_support_readiness,
        tl_kpi=tl_kpi,
        tl_general=tl_general,
        # Raw data — pre-populated if batch-fetched, else empty (fetch_data_node fills)
        crm_log_records=crm_log_records if crm_log_records is not None else [],
        ticket_records=ticket_records if ticket_records is not None else [],
        attendance_records=attendance_records if attendance_records is not None else [],
        data_prefetched=data_prefetched,
        # Fetch errors
        crm_fetch_error=None,
        tickets_fetch_error=None,
        attendance_fetch_error=None,
        # CRM scores
        total_log_hours=0.0,
        log_hours_score=30.0,
        sentiment_score=60.0,
        average_polarity=0.0,
        crm_log_score=0.0,
        # Ticket scores
        total_tickets=0,
        average_taken_days=0.0,
        monthly_tickets_score=40.0,
        monthly_ticket_resolved_score=60.0,
        tickets_evaluation_score=0.0,
        # Segment A
        monthly_functional_score=0.0,
        segment_a_marks=0.0,
        # Segment B
        attendance_score=0.0,
        attendance_marks=0.0,
        tl_total=0.0,
        segment_b_marks=0.0,
        # Final
        base_total=0.0,
        final_score=0.0,
        # Meta
        persisted=False,
        persist_error=None,
        workflow_error=None,
    )


# ── Node implementations ──────────────────────────────────────────────────────


async def fetch_data_node(state: HajjHelpdeskEvalState) -> dict:
    """
    Fetch CRM log hours, ticket data, and attendance in parallel.

    When ``state["data_prefetched"]`` is True, all three record lists have
    already been populated by a team-wide batch query. In that case this
    node is a no-op.
    """
    email = state["employee_email"]
    employee_id = state["employee_id"]
    year = state["year"]
    month = state["month"]

    if state.get("data_prefetched"):
        logger.info(
            "hajj_helpdesk_fetch_data_skipped_prefetched",
            email=email,
            crm_records=len(state.get("crm_log_records", [])),
            ticket_records=len(state.get("ticket_records", [])),
            attendance_records=len(state.get("attendance_records", [])),
        )
        return {}

    logger.info(
        "hajj_helpdesk_fetch_data_start",
        email=email,
        year=year,
        month=month,
    )

    crm_client = SupportCRMClient()
    tickets_client = SupportTicketsClient()
    hr_client = MySQLHRClient()

    async def _fetch_crm() -> tuple[list, list, str | None]:
        try:
            hours = await crm_client.get_crm_log_hours(
                employee_ids=[employee_id], year=year, month=month
            )
            descriptions = await crm_client.get_crm_descriptions(
                employee_ids=[employee_id], year=year, month=month
            )
            return hours, descriptions, None
        except Exception as exc:
            logger.error("hajj_helpdesk_crm_fetch_failed", email=email, error=str(exc))
            return [], [], str(exc)

    async def _fetch_tickets() -> tuple[list, str | None]:
        try:
            rows = await tickets_client.get_ticket_scores(
                employee_ids=[employee_id], year=year, month=month
            )
            return rows, None
        except Exception as exc:
            logger.error("hajj_helpdesk_tickets_fetch_failed", email=email, error=str(exc))
            return [], str(exc)

    async def _fetch_attendance() -> tuple[list, str | None]:
        try:
            rows = await hr_client.get_attendance(
                employee_ids=[employee_id], year=year, month=month
            )
            return rows, None
        except Exception as exc:
            logger.error("hajj_helpdesk_attendance_fetch_failed", email=email, error=str(exc))
            return [], str(exc)

    (crm_hours, crm_descs, crm_err), (ticket_rows, tick_err), (att_rows, att_err) = (
        await asyncio.gather(
            _fetch_crm(),
            _fetch_tickets(),
            _fetch_attendance(),
        )
    )

    await asyncio.gather(crm_client.close(), tickets_client.close(), hr_client.close())

    crm_log_records = [
        {
            **h,
            "descriptions": [
                d["description"]
                for d in crm_descs
                if (d.get("user_email") or "").lower() == email.lower()
            ],
        }
        for h in crm_hours
        if (h.get("user_email") or "").lower() == email.lower()
    ]

    logger.info(
        "hajj_helpdesk_fetch_data_done",
        email=email,
        crm_records=len(crm_log_records),
        ticket_records=len(ticket_rows),
        attendance_records=len(att_rows),
    )

    return {
        "crm_log_records": crm_log_records,
        "ticket_records": ticket_rows,
        "attendance_records": att_rows,
        "crm_fetch_error": crm_err,
        "tickets_fetch_error": tick_err,
        "attendance_fetch_error": att_err,
    }


async def compute_crm_score_node(state: HajjHelpdeskEvalState) -> dict:
    """
    Compute the CRM log score from log hours and description sentiment.

    Uses the Hajj Helpdesk-team specific log hour tier (>=160→100, etc.).
    """
    email = state["employee_email"]

    total_hours = sum(r.get("total_hours", 0.0) for r in state["crm_log_records"])

    all_descriptions: list[str] = []
    for record in state["crm_log_records"]:
        all_descriptions.extend(record.get("descriptions", []))

    avg_sentiment, avg_polarity = compute_employee_sentiment_score(all_descriptions)

    log_hours_score, crm_log_score = compute_crm_log_score(total_hours, avg_sentiment)

    logger.info(
        "hajj_helpdesk_crm_score_computed",
        email=email,
        total_log_hours=total_hours,
        log_hours_score=log_hours_score,
        sentiment_score=avg_sentiment,
        crm_log_score=crm_log_score,
    )

    return {
        "total_log_hours": total_hours,
        "log_hours_score": log_hours_score,
        "sentiment_score": avg_sentiment,
        "average_polarity": avg_polarity,
        "crm_log_score": crm_log_score,
    }


async def compute_tickets_node(state: HajjHelpdeskEvalState) -> dict:
    """
    Compute the ticket evaluation score from ticket count and resolution speed.
    """
    email = state["employee_email"]

    ticket_row = next(
        (
            r
            for r in state["ticket_records"]
            if (r.get("user_email") or "").lower() == email.lower()
        ),
        None,
    )

    if ticket_row is None:
        logger.info(
            "hajj_helpdesk_tickets_no_row_found",
            email=email,
        )
        return {
            "total_tickets": 0,
            "average_taken_days": 0.0,
            "monthly_tickets_score": 0.0,
            "monthly_ticket_resolved_score": 0.0,
            "tickets_evaluation_score": 0.0,
        }

    total_tickets = int(ticket_row.get("total_tickets", 0))
    avg_days = float(ticket_row.get("average_taken_days", 0.0))

    volume_score, speed_score, tickets_eval = compute_tickets_evaluation_score(
        total_tickets, avg_days
    )

    logger.info(
        "hajj_helpdesk_tickets_score_computed",
        email=email,
        total_tickets=total_tickets,
        avg_days=avg_days,
        volume_score=volume_score,
        speed_score=speed_score,
        tickets_evaluation_score=tickets_eval,
    )

    return {
        "total_tickets": total_tickets,
        "average_taken_days": avg_days,
        "monthly_tickets_score": volume_score,
        "monthly_ticket_resolved_score": speed_score,
        "tickets_evaluation_score": tickets_eval,
    }


async def compute_functional_score_node(state: HajjHelpdeskEvalState) -> dict:
    """
    Compute monthly_functional_score and segment_a_marks.

    monthly_functional = crm_log_score*0.8 + tickets_evaluation*0.2
    segment_a_marks    = monthly_functional * 0.30  (max 30)
    """
    email = state["employee_email"]

    functional = compute_functional_score(
        crm_log_score=state["crm_log_score"],
        tickets_evaluation_score=state["tickets_evaluation_score"],
    )
    segment_a = compute_segment_a_marks(functional)

    logger.info(
        "hajj_helpdesk_functional_score_computed",
        email=email,
        monthly_functional_score=functional,
        segment_a_marks=segment_a,
    )

    return {
        "monthly_functional_score": functional,
        "segment_a_marks": segment_a,
    }


async def compute_segment_b_node(state: HajjHelpdeskEvalState) -> dict:
    """
    Compute Segment B marks from attendance and TL assessment.

    attendance_marks = attendance_score / 10   (max 10)
    tl_total         = readiness + kpi + gen   (max 40)
    segment_b_marks  = attendance + tl         (max 50)
    """
    email = state["employee_email"]

    att_row = next(
        (
            r
            for r in state["attendance_records"]
            if (r.get("user_email") or "").lower() == email.lower()
        ),
        None,
    )
    attendance_score = float(att_row.get("attendance_score", 0.0)) if att_row else 0.0
    attendance_marks = compute_attendance_marks(attendance_score)

    tl_total = compute_tl_total(
        support_readiness=state["tl_support_readiness"],
        kpi=state["tl_kpi"],
        general=state["tl_general"],
    )
    segment_b = compute_segment_b_marks(attendance_marks, tl_total)

    logger.info(
        "hajj_helpdesk_segment_b_computed",
        email=email,
        attendance_score=attendance_score,
        attendance_marks=attendance_marks,
        tl_total=tl_total,
        segment_b_marks=segment_b,
    )

    return {
        "attendance_score": attendance_score,
        "attendance_marks": attendance_marks,
        "tl_total": tl_total,
        "segment_b_marks": segment_b,
    }


async def finalize_score_node(state: HajjHelpdeskEvalState) -> dict:
    """
    Compute final score.

    base_total  = segment_a + segment_b  (max 80)
    final_score = (base_total / 80) * 100

    No reward marks for hajj helpdesk team.
    """
    email = state["employee_email"]

    base_total, final_score = compute_hajj_helpdesk_final_score(
        segment_a_marks=state["segment_a_marks"],
        segment_b_marks=state["segment_b_marks"],
    )

    logger.info(
        "hajj_helpdesk_final_score_computed",
        email=email,
        segment_a=state["segment_a_marks"],
        segment_b=state["segment_b_marks"],
        base_total=base_total,
        final_score=final_score,
    )

    return {
        "base_total": base_total,
        "final_score": final_score,
    }


async def persist_results_node(state: HajjHelpdeskEvalState, db: AsyncSession) -> dict:
    """
    Write computed scores to the SQLite application database.

    Upserts three rows:
      - SupportCRMLogScore
      - SupportTicketScore
      - SupportFinalScore
    """
    email = state["employee_email"]
    year = state["year"]
    month = state["month"]
    run_id = state["evaluation_run_id"]

    try:
        crm_row = SupportCRMLogScore(
            evaluation_run_id=run_id,
            employee_email=email,
            year=year,
            month=month,
            total_log_hours=state["total_log_hours"],
            total_log_entries=sum(
                len(r.get("descriptions", [])) for r in state["crm_log_records"]
            ),
            log_hours_score=state["log_hours_score"],
            sentiment_score=state["sentiment_score"],
            average_sentiment_polarity=state["average_polarity"],
            crm_log_score=state["crm_log_score"],
        )
        db.add(crm_row)

        ticket_row = SupportTicketScore(
            evaluation_run_id=run_id,
            employee_email=email,
            year=year,
            month=month,
            total_tickets=state["total_tickets"],
            average_taken_days=state["average_taken_days"],
            monthly_tickets_score=state["monthly_tickets_score"],
            monthly_ticket_resolved_score=state["monthly_ticket_resolved_score"],
            tickets_evaluation_score=state["tickets_evaluation_score"],
        )
        db.add(ticket_row)

        final_row = SupportFinalScore(
            evaluation_run_id=run_id,
            employee_email=email,
            year=year,
            month=month,
            total_log_hours=state["total_log_hours"],
            log_hours_score=state["log_hours_score"],
            sentiment_score=state["sentiment_score"],
            crm_log_score=state["crm_log_score"],
            total_tickets=state["total_tickets"],
            average_taken_days=state["average_taken_days"],
            tickets_evaluation_score=state["tickets_evaluation_score"],
            monthly_functional_score=state["monthly_functional_score"],
            segment_a_marks=state["segment_a_marks"],
            attendance_score=state["attendance_score"],
            attendance_marks=state["attendance_marks"],
            support_readiness=state["tl_support_readiness"],
            kpi=state["tl_kpi"],
            general=state["tl_general"],
            tl_total=state["tl_total"],
            segment_b_marks=state["segment_b_marks"],
            base_total=state["base_total"],
            final_score=state["final_score"],
        )
        db.add(final_row)

        await db.commit()
        logger.info("hajj_helpdesk_scores_persisted", email=email)
        return {"persisted": True, "persist_error": None}

    except Exception as exc:
        await db.rollback()
        logger.error("hajj_helpdesk_scores_persist_failed", email=email, error=str(exc))
        return {"persisted": False, "persist_error": str(exc)}


# ── Graph construction ────────────────────────────────────────────────────────


def _build_hajj_helpdesk_workflow() -> StateGraph:
    """Build and compile the Hajj Helpdesk evaluation StateGraph."""

    graph = StateGraph(HajjHelpdeskEvalState)

    graph.add_node("fetch_data", fetch_data_node)
    graph.add_node("compute_crm_score", compute_crm_score_node)
    graph.add_node("compute_tickets", compute_tickets_node)
    graph.add_node("compute_functional", compute_functional_score_node)
    graph.add_node("compute_segment_b", compute_segment_b_node)
    graph.add_node("finalize_score", finalize_score_node)

    graph.add_edge(START, "fetch_data")
    graph.add_edge("fetch_data", "compute_crm_score")
    graph.add_edge("fetch_data", "compute_tickets")
    graph.add_edge("compute_crm_score", "compute_functional")
    graph.add_edge("compute_tickets", "compute_functional")
    graph.add_edge("compute_functional", "compute_segment_b")
    graph.add_edge("compute_segment_b", "finalize_score")
    graph.add_edge("finalize_score", END)

    return graph.compile()


# Compiled graph — instantiated once at module level
_hajj_helpdesk_workflow = _build_hajj_helpdesk_workflow()


# ── Public entry point ────────────────────────────────────────────────────────


async def run_hajj_helpdesk_evaluation(
    *,
    employee_email: str,
    employee_id: str,
    evaluation_run_id: int,
    year: int,
    month: int,
    team: str,
    tl_support_readiness: float,
    tl_kpi: float,
    tl_general: float,
    db: AsyncSession,
    prefetched_crm_log_records: list | None = None,
    prefetched_ticket_records: list | None = None,
    prefetched_attendance_records: list | None = None,
) -> HajjHelpdeskEvalState:
    """
    Execute the full Hajj Helpdesk team evaluation workflow for one employee.

    Args:
        employee_email:      Work email address (used for MySQL + DB lookups).
        employee_id:         HR employee ID string (used for MySQL IN clause).
        evaluation_run_id:   FK for evaluation_runs table.
        year, month:         Evaluation period.
        team:                Team key.
        tl_support_readiness: TL mark for Support Readiness & Issue Handling (0-10).
        tl_kpi:              TL mark for KPI Agreement (0-15).
        tl_general:          TL mark for Leadership General Assessment (0-15).
        db:                  Async SQLAlchemy session for persisting results.
        prefetched_crm_log_records:  Pre-fetched CRM log records.
        prefetched_ticket_records:   Pre-fetched ticket data.
        prefetched_attendance_records: Pre-fetched attendance data.

    Returns:
        Final HajjHelpdeskEvalState with all computed scores.
    """
    use_prefetch = (
        prefetched_crm_log_records is not None
        or prefetched_ticket_records is not None
        or prefetched_attendance_records is not None
    )
    initial_state = _default_state(
        employee_email=employee_email,
        employee_id=employee_id,
        evaluation_run_id=evaluation_run_id,
        year=year,
        month=month,
        team=team,
        tl_support_readiness=tl_support_readiness,
        tl_kpi=tl_kpi,
        tl_general=tl_general,
        crm_log_records=prefetched_crm_log_records,
        ticket_records=prefetched_ticket_records,
        attendance_records=prefetched_attendance_records,
        data_prefetched=use_prefetch,
    )

    final_state: HajjHelpdeskEvalState = await _hajj_helpdesk_workflow.ainvoke(initial_state)

    persist_update = await persist_results_node(final_state, db)
    final_state = {**final_state, **persist_update}

    return final_state
