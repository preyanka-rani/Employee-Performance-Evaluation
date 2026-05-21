"""
app/services/support_teams/workflows/state.py
──────────────────────────────────────────────
TypedDict definitions for the Support Team LangGraph evaluation workflow state.

The state flows through all nodes sequentially and accumulates results.
Each node reads from the state, does its work, and returns a partial update.
"""

from __future__ import annotations

from typing import Any, TypedDict


class SupportEvalState(TypedDict):
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
