"""
app/schemas/evaluation.py
──────────────────────────
Schemas for evaluation run trigger, status responses, and results.
"""

from datetime import datetime

from pydantic import BaseModel, Field

from app.models.evaluation_run import EvaluationStatus


class EvaluationRunRequest(BaseModel):
    """Request body to trigger a manual evaluation run."""

    team: str = Field(..., examples=["developer"])
    year: int = Field(..., ge=2020, le=2100, examples=[2026])
    month: int = Field(..., ge=1, le=12, examples=[4])


class EvaluationRunResponse(BaseModel):
    id: int
    team: str
    year: int
    month: int
    status: EvaluationStatus
    triggered_by: str
    error_message: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class EvaluationStatusResponse(BaseModel):
    id: int
    status: EvaluationStatus
    team: str
    year: int
    month: int
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error_message: str | None = None

    model_config = {"from_attributes": True}
