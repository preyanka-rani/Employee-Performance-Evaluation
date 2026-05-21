"""
app/models/support_scores.py
──────────────────────────────
SQLAlchemy ORM models for Support Team evaluation scores.

Covers: Impl & ITS, Onsite Support, Production, Tech Support.

Score pipeline per employee:
  support_crm_log_scores    ← log hours + sentiment from project_activity_log
  support_ticket_scores     ← ticket count + resolution days from process_list_hist
  support_functional_scores ← merged functional score (CRM*0.8 + Tickets*0.2)
  support_final_scores      ← complete breakdown including TL, attendance, final
"""

from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class SupportCRMLogScore(Base):
    """
    Per-employee CRM activity log score for the support team.

    Sources: project_activity_log (MySQL CRM DB)
    Formula:
        log_hours_score = tiered normalisation (>=160→100, >=140→80, ...)
        crm_log_score   = log_hours_score * 0.9 + sentiment_score * 0.1
    """

    __tablename__ = "support_crm_log_scores"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    evaluation_run_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("evaluation_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    employee_email: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    month: Mapped[int] = mapped_column(Integer, nullable=False)

    # Raw data
    total_log_hours: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    total_log_entries: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Computed scores
    log_hours_score: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0
    )  # 0-100
    sentiment_score: Mapped[float] = mapped_column(
        Float, nullable=False, default=60.0
    )  # 0-100
    average_sentiment_polarity: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0
    )

    # Final CRM score: log_hours_score*0.9 + sentiment_score*0.1
    crm_log_score: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0
    )  # 0-100

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return (
            f"<SupportCRMLogScore email={self.employee_email!r} "
            f"{self.year}/{self.month:02d} crm_score={self.crm_log_score}>"
        )


class SupportTicketScore(Base):
    """
    Per-employee ticket handling score for the support team.

    Source: process_list_hist (MySQL CRM DB)
    Scoring:
        monthly_tickets_score:
            >=30 → 100 | >=20 → 80 | >=10 → 70 | >0 → 60 | =0 → 40
        monthly_ticket_resolved_score:
            avg_taken_days <= 2 → 100 | else → 60
        tickets_evaluation_score = tickets_score*0.7 + resolved_score*0.3
    """

    __tablename__ = "support_ticket_scores"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    evaluation_run_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("evaluation_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    employee_email: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    month: Mapped[int] = mapped_column(Integer, nullable=False)

    # Raw data
    total_tickets: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    average_taken_days: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0
    )

    # Computed scores
    monthly_tickets_score: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0
    )  # 0-100
    monthly_ticket_resolved_score: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0
    )  # 0-100

    # Final ticket evaluation: tickets_score*0.7 + resolved_score*0.3
    tickets_evaluation_score: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0
    )  # 0-100

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return (
            f"<SupportTicketScore email={self.employee_email!r} "
            f"{self.year}/{self.month:02d} tickets={self.total_tickets} "
            f"eval_score={self.tickets_evaluation_score}>"
        )


class SupportFinalScore(Base):
    """
    Complete evaluation breakdown and final score for a support team employee.

    Scoring breakdown (max 100):
        ┌─ Segment A: Functional Performance (max 30)
        │   monthly_functional_score = crm_log_score*0.8 + tickets_eval*0.2
        │   segment_a_marks = monthly_functional_score * 0.30  → 0-30
        │
        ├─ Segment B: Discipline & Leadership (max 50)
        │   attendance_marks  = attendance_score / 10          → 0-10
        │   support_readiness = TL score                       → 0-10
        │   kpi               = TL score                       → 0-15
        │   general           = TL score                       → 0-15
        │   segment_b_marks   = attendance_marks + tl_marks    → 0-50
        │
        ├─ Base Total = segment_a + segment_b                  → 0-80
        │
        └─ Final Score = (base_total / 80) * 100               → 0-100
    """

    __tablename__ = "support_final_scores"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    evaluation_run_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("evaluation_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    employee_email: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    month: Mapped[int] = mapped_column(Integer, nullable=False)

    # ── Segment A sub-scores ──────────────────────────────────────────────────
    total_log_hours: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    log_hours_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    sentiment_score: Mapped[float] = mapped_column(Float, nullable=False, default=60.0)
    crm_log_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    total_tickets: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    average_taken_days: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0
    )
    tickets_evaluation_score: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0
    )

    monthly_functional_score: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0
    )
    segment_a_marks: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0
    )  # 0-30

    # ── Segment B sub-scores ──────────────────────────────────────────────────
    attendance_score: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0
    )  # 0-100
    attendance_marks: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0
    )  # 0-10
    support_readiness: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0
    )  # 0-10
    kpi: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)  # 0-15
    general: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)  # 0-15
    tl_total: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)  # 0-40
    segment_b_marks: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0
    )  # 0-50

    # ── Final ─────────────────────────────────────────────────────────────────
    base_total: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0
    )  # 0-80
    final_score: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0
    )  # 0-100

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return (
            f"<SupportFinalScore email={self.employee_email!r} "
            f"{self.year}/{self.month:02d} final={self.final_score}>"
        )
