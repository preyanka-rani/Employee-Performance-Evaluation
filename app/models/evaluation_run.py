"""
app/models/evaluation_run.py
─────────────────────────────
Tracks each monthly evaluation execution cycle.
One EvaluationRun covers an entire team for a given year/month.
"""

from datetime import datetime, timezone
from enum import Enum

from sqlalchemy import DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class EvaluationStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    PARTIAL = "partial"  # some employees failed, others succeeded


class EvaluationRun(Base):
    """
    A single execution of the evaluation pipeline for one team+month.

    The pipeline can be triggered:
      - Automatically by the Celery monthly scheduler.
      - Manually via POST /api/v1/evaluations/run.
    """

    __tablename__ = "evaluation_runs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    year: Mapped[int] = mapped_column(nullable=False)
    month: Mapped[int] = mapped_column(nullable=False)
    team: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    status: Mapped[EvaluationStatus] = mapped_column(
        String(20),
        nullable=False,
        default=EvaluationStatus.PENDING,
    )
    triggered_by: Mapped[str] = mapped_column(
        String(50), nullable=False, default="system"
    )  # 'system' | 'api' | 'manual'
    error_message: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    def __repr__(self) -> str:
        return (
            f"<EvaluationRun id={self.id} team={self.team!r} "
            f"{self.year}/{self.month:02d} status={self.status}>"
        )
