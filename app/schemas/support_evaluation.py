"""
app/schemas/support_evaluation.py
────────────────────────────────────
Pydantic schemas for support team evaluation API requests and responses.

Used by:
    app/api/v1/support_evaluations.py
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class SupportEmployeeResult(BaseModel):
    """Per-employee result included in the bulk-run response."""

    employee_id: str
    employee_email: str
    # CRM Log component
    total_log_hours: float = 0.0
    log_hours_score: float = 0.0
    sentiment_score: float = 0.0
    crm_log_score: float = 0.0
    # Tickets component
    total_tickets: int = 0
    average_taken_days: float = 0.0
    tickets_evaluation_score: float = 0.0
    # Aggregates
    monthly_functional_score: float = 0.0
    segment_a_marks: float = 0.0
    # Segment B
    attendance_score: float = 0.0
    attendance_marks: float = 0.0
    tl_support_readiness: float = 0.0
    tl_kpi: float = 0.0
    tl_general: float = 0.0
    tl_total: float = 0.0
    segment_b_marks: float = 0.0
    # Final
    base_total: float = 0.0
    reward_score: float = 0.0  # Always 0 for support teams
    final_score: float = 0.0
    error: str | None = None


class SupportBulkRunResponse(BaseModel):
    """Response from POST /api/v1/support-evaluations/bulk-run."""

    run_id: int
    team: str
    year: int
    month: int
    status: str
    processed_count: int = 0
    failed_count: int = 0
    report_path: str | None = None
    results: list[SupportEmployeeResult] = Field(default_factory=list)
    errors: list[dict] = Field(default_factory=list)


class SupportEvaluationStatusResponse(BaseModel):
    """Response from GET /api/v1/support-evaluations/{run_id}."""

    run_id: int
    team: str
    year: int
    month: int
    status: str
    error_message: str | None = None
