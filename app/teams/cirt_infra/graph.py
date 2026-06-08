"""
app/teams/cirt_infra/graph.py
─────────────────────────────
CIRT & Infra worker LangGraph — wraps the per-employee scoring flow derived
from the legacy ``cirt_infra_evaluation.ipynb`` reference notebook and the
authoritative rules in
``documentation/Employee Performance Evaluation___.md`` (Section 4).

Pipeline
────────

    START
      ↓
    fetch_crm        — CRM work log hours + descriptions (asyncio.gather)
      ↓
    fetch_hr         — MySQL HR attendance
      ↓
    load_tl          — load TLAssessmentScore from SQLite
      ↓
    compute_functional — log_hours_score, sentiment, monthly_functional, segment_a_marks
      ↓
    compute_segment_b — attendance_marks, TL total, segment_b_marks
      ↓
    compute_final     — base_total, final_score  (no reward marks)
      ↓
    END

The graph is **pure** — no DB writes happen inside nodes. All persistence
is performed by ``CIRTInfraTeam.run_per_employee()`` after the graph
returns, in the same order as the legacy notebook's evaluation.
"""

from __future__ import annotations

from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging_config import get_logger
from app.repositories.score_repository import TLAssessmentRepository
from app.services.ai.sentiment import compute_employee_sentiment_score
from app.shared.data_sources.mysql_client import MySQLHRClient
from app.shared.data_sources.support_crm_client import SupportCRMClient
from app.teams.cirt_infra.formulas import (
    compute_attendance_marks,
    compute_cirt_final_score,
    compute_cirt_functional_score,
    compute_segment_a_marks,
    compute_segment_b_marks,
    compute_tl_total,
)

logger = get_logger(__name__)


# ── Workflow state ────────────────────────────────────────────────────────────


class CIRTInfraWorkerState(TypedDict, total=False):
    # ── Inputs (set by run_per_employee before invoking) ──────────────────────
    employee_id: str
    employee_email: str
    employee_name: str
    year: int
    month: int
    run_id: int
    db: AsyncSession  # passed for TL lookup only (graph doesn't write)

    # ── After fetch_crm_node ─────────────────────────────────────────────────
    crm_log_records: list[dict]
    crm_description_records: list[dict]

    # ── After fetch_hr_node ──────────────────────────────────────────────────
    attendance_records: list[dict]

    # ── After load_tl_node ───────────────────────────────────────────────────
    tl_support_readiness: float
    tl_kpi: float
    tl_general: float

    # ── After compute_functional_node ────────────────────────────────────────
    total_hours: float
    log_hours_score: float
    sentiment_avg: float
    avg_polarity: float
    monthly_functional_score: float
    segment_a_marks: float

    # ── After compute_segment_b_node ─────────────────────────────────────────
    attendance_score: float
    attendance_present: int
    attendance_late: int
    attendance_work_days: int
    attendance_marks: float
    tl_total: float
    segment_b_marks: float

    # ── After compute_final_node ─────────────────────────────────────────────
    base_total: float
    final_score: float

    # ── Error tracking ───────────────────────────────────────────────────────
    fetch_error: str | None


# ── Node implementations ──────────────────────────────────────────────────────


async def fetch_crm_node(state: CIRTInfraWorkerState) -> dict:
    """
    Fetch CRM work log hours and descriptions from MySQL.

    The CIRT & Infra team uses the exact same MySQL tables as the support
    team (``project_activity_log`` and ``project_activity_log_clab``), so
    we reuse the existing ``SupportCRMClient`` (its SQL is byte-identical
    to the docs §4.1 query).
    """
    import asyncio

    log = logger.bind(employee_email=state["employee_email"])
    log.info("cirt_fetch_crm_start")

    crm = SupportCRMClient()
    try:
        log_records, descriptions = await asyncio.gather(
            crm.get_crm_log_hours(
                employee_ids=[state["employee_id"]],
                year=state["year"],
                month=state["month"],
            ),
            crm.get_crm_descriptions(
                employee_ids=[state["employee_id"]],
                year=state["year"],
                month=state["month"],
            ),
            return_exceptions=False,
        )
    except Exception as exc:
        log.error("crm_fetch_failed", error=str(exc))
        return {
            "crm_log_records": [],
            "crm_description_records": [],
        }
    finally:
        await crm.close()

    log.info(
        "cirt_fetch_crm_done",
        log_records=len(log_records),
        descriptions=len(descriptions),
    )
    return {
        "crm_log_records": log_records,
        "crm_description_records": descriptions,
    }


async def fetch_hr_node(state: CIRTInfraWorkerState) -> dict:
    """Fetch attendance from MySQL HR (monthly_attendance_summary)."""
    log = logger.bind(employee_email=state["employee_email"])
    log.info("cirt_fetch_hr_start")

    hr = MySQLHRClient()
    try:
        attendance_records = await hr.get_attendance(
            employee_ids=[state["employee_id"]],
            year=state["year"],
            month=state["month"],
        )
    except Exception as exc:
        log.error("hr_fetch_failed", error=str(exc))
        return {"attendance_records": []}
    finally:
        await hr.close()

    log.info(
        "cirt_fetch_hr_done",
        attendance_records=len(attendance_records),
    )
    return {"attendance_records": attendance_records}


async def load_tl_node(state: CIRTInfraWorkerState) -> dict:
    """
    Load TL assessment from SQLite. Falls back to mid-range defaults
    (5.0 / 7.5 / 7.5) if no row exists for this period.

    For CIRT, the TL "Support Readiness" column lands in the
    ``TLAssessmentScore.problem_solving`` slot (legacy mapping, set by
    the upsert helper when ``use_support_readiness=True``).
    """
    log = logger.bind(employee_email=state["employee_email"])
    db: AsyncSession = state["db"]

    tl_repo = TLAssessmentRepository(db)
    tl_score = await tl_repo.get_for_employee_period(
        employee_email=state["employee_email"],
        year=state["year"],
        month=state["month"],
        evaluation_run_id=state["run_id"],
    )

    if tl_score:
        support_readiness = float(tl_score.problem_solving)
        kpi = float(tl_score.kpi)
        general = float(tl_score.general)
    else:
        log.warning("tl_score_not_found_using_defaults")
        support_readiness = 5.0
        kpi = 7.5
        general = 7.5

    log.info(
        "tl_loaded",
        support_readiness=support_readiness,
        kpi=kpi,
        general=general,
    )
    return {
        "tl_support_readiness": support_readiness,
        "tl_kpi": kpi,
        "tl_general": general,
    }


async def compute_functional_node(state: CIRTInfraWorkerState) -> dict:
    """
    Compute Segment A from work logs + sentiment.

    Mirrors docs §4.1: log hours tier + TextBlob sentiment, weighted
    0.9 / 0.1, then scaled to 0-30 marks.
    """
    log = logger.bind(employee_email=state["employee_email"])

    # Sum hours for this employee (filter by exact employee_id match)
    total_hours = sum(
        float(row.get("total_hours", 0))
        for row in state["crm_log_records"]
        if str(row.get("employee_id", "")) == state["employee_id"]
    )
    from app.teams.cirt_infra.formulas import normalise_cirt_log_hours

    log_hours_score = normalise_cirt_log_hours(total_hours)

    # Sentiment from descriptions
    desc_texts = [
        str(row.get("description", ""))
        for row in state["crm_description_records"]
        if str(row.get("employee_id", "")) == state["employee_id"]
    ]
    sentiment_avg, avg_polarity = compute_employee_sentiment_score(desc_texts)

    monthly_functional_score = compute_cirt_functional_score(
        log_hours=total_hours,
        sentiment_score=sentiment_avg,
    )
    segment_a_marks = compute_segment_a_marks(monthly_functional_score)

    log.info(
        "cirt_functional_computed",
        total_hours=total_hours,
        log_hours_score=log_hours_score,
        sentiment_score=sentiment_avg,
        monthly_functional_score=monthly_functional_score,
        segment_a_marks=segment_a_marks,
    )
    return {
        "total_hours": total_hours,
        "log_hours_score": log_hours_score,
        "sentiment_avg": sentiment_avg,
        "avg_polarity": avg_polarity,
        "monthly_functional_score": monthly_functional_score,
        "segment_a_marks": segment_a_marks,
    }


async def compute_segment_b_node(state: CIRTInfraWorkerState) -> dict:
    """
    Compute Segment B from attendance + TL scores.

    Mirrors docs §4.2: attendance_score/10 + support_readiness + kpi + general.
    """
    log = logger.bind(employee_email=state["employee_email"])

    att_row = next(
        (
            r
            for r in state["attendance_records"]
            if str(r.get("employee_id", "")) == state["employee_id"]
        ),
        None,
    )

    if att_row:
        attendance_score = float(att_row.get("attendance_score", 60.0))
        present = int(att_row.get("present", 0))
        late = int(att_row.get("late", 0))
        actual_work_days = int(att_row.get("actual_work_days", 22))
    else:
        attendance_score = 60.0
        present = 0
        late = 0
        actual_work_days = 22

    attendance_marks = compute_attendance_marks(attendance_score)
    tl_total = compute_tl_total(
        support_readiness=state["tl_support_readiness"],
        kpi=state["tl_kpi"],
        general=state["tl_general"],
    )
    segment_b_marks = compute_segment_b_marks(attendance_marks, tl_total)

    log.info(
        "cirt_segment_b_computed",
        attendance_score=attendance_score,
        attendance_marks=attendance_marks,
        tl_total=tl_total,
        segment_b_marks=segment_b_marks,
    )
    return {
        "attendance_score": attendance_score,
        "attendance_present": present,
        "attendance_late": late,
        "attendance_work_days": actual_work_days,
        "attendance_marks": attendance_marks,
        "tl_total": tl_total,
        "segment_b_marks": segment_b_marks,
    }


async def compute_final_node(state: CIRTInfraWorkerState) -> dict:
    """Compute base_total and final_score (docs §4.3)."""
    log = logger.bind(employee_email=state["employee_email"])

    base_total, final_score = compute_cirt_final_score(
        segment_a_marks=state["segment_a_marks"],
        segment_b_marks=state["segment_b_marks"],
    )

    log.info(
        "cirt_final_computed",
        base_total=base_total,
        final_score=final_score,
    )
    return {
        "base_total": base_total,
        "final_score": final_score,
    }


# ── Graph construction ────────────────────────────────────────────────────────


def build_cirt_infra_graph() -> Any:
    """Build and compile the CIRT & Infra worker StateGraph."""
    builder = StateGraph(CIRTInfraWorkerState)

    builder.add_node("fetch_crm", fetch_crm_node)
    builder.add_node("fetch_hr", fetch_hr_node)
    builder.add_node("load_tl", load_tl_node)
    builder.add_node("compute_functional", compute_functional_node)
    builder.add_node("compute_segment_b", compute_segment_b_node)
    builder.add_node("compute_final", compute_final_node)

    builder.add_edge(START, "fetch_crm")
    builder.add_edge("fetch_crm", "fetch_hr")
    builder.add_edge("fetch_hr", "load_tl")
    builder.add_edge("load_tl", "compute_functional")
    builder.add_edge("compute_functional", "compute_segment_b")
    builder.add_edge("compute_segment_b", "compute_final")
    builder.add_edge("compute_final", END)

    return builder.compile()


# Singleton compiled graph — instantiated once at module load.
_cirt_infra_graph = build_cirt_infra_graph()


async def run_cirt_infra_worker(
    *,
    employee_id: str,
    employee_email: str,
    employee_name: str,
    evaluation_run_id: int,
    year: int,
    month: int,
    db: AsyncSession,
) -> CIRTInfraWorkerState:
    """
    Run the full CIRT & Infra worker graph for one employee.

    Returns the final CIRTInfraWorkerState with all computed scores.
    Persistence (DB row writes) is the caller's responsibility (see
    ``CIRTInfraTeam.run_per_employee``).
    """
    initial_state: CIRTInfraWorkerState = {
        "employee_id": employee_id,
        "employee_email": employee_email,
        "employee_name": employee_name,
        "year": year,
        "month": month,
        "run_id": evaluation_run_id,
        "db": db,
        # Output defaults (so partial-state updates don't lose them)
        "crm_log_records": [],
        "crm_description_records": [],
        "attendance_records": [],
        "tl_support_readiness": 5.0,
        "tl_kpi": 7.5,
        "tl_general": 7.5,
        "total_hours": 0.0,
        "log_hours_score": 0.0,
        "sentiment_avg": 60.0,
        "avg_polarity": 0.0,
        "monthly_functional_score": 0.0,
        "segment_a_marks": 0.0,
        "attendance_score": 60.0,
        "attendance_present": 0,
        "attendance_late": 0,
        "attendance_work_days": 22,
        "attendance_marks": 6.0,
        "tl_total": 20.0,
        "segment_b_marks": 26.0,
        "base_total": 0.0,
        "final_score": 0.0,
        "fetch_error": None,
    }
    final_state: CIRTInfraWorkerState = await _cirt_infra_graph.ainvoke(initial_state)  # type: ignore[assignment]
    return final_state
