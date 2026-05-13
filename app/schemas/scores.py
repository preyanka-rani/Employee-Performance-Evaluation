"""
app/schemas/scores.py
──────────────────────
Pydantic schemas for all scoring components and final results.
Mirrors the ORM models in app/models/scores.py but is decoupled from the DB layer.
"""

from datetime import datetime

from pydantic import BaseModel, Field

# ── TL Assessment upload ──────────────────────────────────────────────────────


class TLAssessmentRow(BaseModel):
    """One row from the uploaded Excel file."""

    employee_email: str = Field(..., examples=["john.doe@company.com"])
    problem_solving: float = Field(..., ge=0, le=10)
    kpi: float = Field(..., ge=0, le=15)
    general: float = Field(..., ge=0, le=15)


class TLAssessmentUploadResponse(BaseModel):
    evaluation_run_id: int
    rows_saved: int
    duplicates_skipped: int
    errors: list[str] = []


# ── Code quality ──────────────────────────────────────────────────────────────


class CodeQualityScoreResponse(BaseModel):
    id: int
    employee_email: str
    mr_reference: str
    raw_score: float
    reasoning: str
    issues: list[str]
    model_used: str
    analyzed_at: datetime

    model_config = {"from_attributes": True}


# ── Attendance ────────────────────────────────────────────────────────────────


class AttendanceScoreResponse(BaseModel):
    employee_email: str
    year: int
    month: int
    score: float
    present_days: int
    actual_work_days: int
    late_days: int

    model_config = {"from_attributes": True}


# ── Final score ───────────────────────────────────────────────────────────────


class FinalScoreResponse(BaseModel):
    id: int
    employee_email: str
    year: int
    month: int

    # Segment A
    quality_check_score: float
    component2_score: float
    segment_a_score: float
    segment_a_marks: float

    # Segment B
    attendance_score: float
    attendance_marks: float
    problem_solving: float
    kpi: float
    general_assessment: float
    tl_total: float
    segment_b_marks: float

    # Final
    base_total: float
    reward_score: float
    final_score: float
    created_at: datetime

    model_config = {"from_attributes": True}
