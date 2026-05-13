"""
app/models/scores.py
─────────────────────
All intermediate and final scoring tables stored in SQLite.

Table hierarchy for Developer evaluation:
  code_quality_scores    ← one row per MR analysed by AI
  attendance_scores      ← one row per employee per month
  sentiment_scores       ← one row per employee per month (log description quality)
  tl_assessment_scores   ← one row per employee per month (manual TL entry)
  final_scores           ← one row per employee per month (computed composite)
"""

from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class CodeQualityScore(Base):
    """
    AI-generated code quality score for a single GitLab Merge Request.
    One developer can have many MRs; we aggregate later in the scoring engine.
    """

    __tablename__ = "code_quality_scores"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    evaluation_run_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("evaluation_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    employee_email: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    mr_reference: Mapped[str] = mapped_column(
        String(200), nullable=False
    )  # e.g. "group/project!42"
    mr_title: Mapped[str | None] = mapped_column(String(500), nullable=True)
    raw_score: Mapped[float] = mapped_column(Float, nullable=False)  # 0-100
    readability_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    logic_efficiency_score: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0
    )
    error_handling_score: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0
    )
    architecture_score: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0
    )
    security_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    reasoning: Mapped[str] = mapped_column(Text, nullable=False)
    issues: Mapped[str] = mapped_column(Text, nullable=False, default="[]")  # JSON list
    model_used: Mapped[str] = mapped_column(
        String(100), nullable=False
    )  # 'claude-sonnet-4-5' | 'llama-3.3-70b-versatile'
    analyzed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return f"<CodeQualityScore mr={self.mr_reference!r} score={self.raw_score}>"


class AttendanceScore(Base):
    """
    Attendance score derived from MySQL monthly_attendance_summary.
    Formula: LEAST(ROUND(((present - FLOOR(late/3)) / actual_work_days) * 100, 2), 100)
    """

    __tablename__ = "attendance_scores"

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
    # Raw attendance data stored for auditability
    present_days: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    work_days: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    actual_work_days: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    late_attendance: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    late_days: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Computed score (0-100)
    score: Mapped[float] = mapped_column(Float, nullable=False)

    def __repr__(self) -> str:
        return (
            f"<AttendanceScore email={self.employee_email!r} "
            f"{self.year}/{self.month:02d} score={self.score}>"
        )


class SentimentScore(Base):
    """
    Sentiment analysis score of work-log descriptions.
    Computed via TextBlob polarity → discrete 3-tier mapping:
      polarity == 1  → 100
      polarity == 0  → 60
      otherwise      → 40
    """

    __tablename__ = "sentiment_scores"

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
    total_logs_analyzed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    average_polarity: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    score: Mapped[float] = mapped_column(Float, nullable=False)  # 0-100

    def __repr__(self) -> str:
        return (
            f"<SentimentScore email={self.employee_email!r} "
            f"{self.year}/{self.month:02d} score={self.score}>"
        )


class WorkLogScore(Base):
    """
    Work log hours score derived from MySQL project_activity_log.
    Log hours are normalised using the custom transformation function.
    """

    __tablename__ = "work_log_scores"

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
    total_log_hours: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    normalized_score: Mapped[float] = mapped_column(Float, nullable=False)  # 0-100

    def __repr__(self) -> str:
        return (
            f"<WorkLogScore email={self.employee_email!r} "
            f"{self.year}/{self.month:02d} hours={self.total_log_hours} "
            f"score={self.normalized_score}>"
        )


class TLAssessmentScore(Base):
    """
    Manual Team Lead assessment scores uploaded via Excel.

    Developer breakdown:
      - problem_solving  : Critical Thinking & Problem Solving (max 10)
      - kpi              : Performance Agreement / KPI (max 15)
      - general          : Team Lead General Assessment (max 15)
      - total            : sum (max 40)
    """

    __tablename__ = "tl_assessment_scores"

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
    problem_solving: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    kpi: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    general: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    total: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    uploaded_by: Mapped[str] = mapped_column(
        String(255), nullable=False, default="system"
    )

    def __repr__(self) -> str:
        return (
            f"<TLAssessmentScore email={self.employee_email!r} "
            f"{self.year}/{self.month:02d} total={self.total}>"
        )


class FinalScore(Base):
    """
    Final composite performance score for one employee for one month.

    Segments (from documentation §1):
      segment_a_score   : (quality_check + component2) / 2  → 0-100 raw
      segment_a_marks   : segment_a_score * 50 / 100        → 0-50
      segment_b_marks   : attendance_marks(10) + tl_marks(40) → 0-50
      base_total        : segment_a_marks + segment_b_marks  → 0-100
      reward_score      : (min(raw_sum, 140) * 5) / 140     → 0-5
      final_score       : ((base_total + reward) / 105) * 100
    """

    __tablename__ = "final_scores"

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

    # Segment A breakdown
    quality_check_score: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0
    )  # 0-100
    component2_score: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0
    )  # 0-100
    segment_a_score: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0
    )  # 0-100
    segment_a_marks: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0
    )  # 0-50

    # Segment B breakdown
    attendance_score: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0
    )  # 0-100
    attendance_marks: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0
    )  # 0-10
    problem_solving: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0
    )  # 0-10
    kpi: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)  # 0-15
    general_assessment: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0
    )  # 0-15
    tl_total: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)  # 0-40
    segment_b_marks: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0
    )  # 0-50

    # Final computation
    base_total: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0
    )  # 0-100
    reward_score: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0
    )  # 0-5
    final_score: Mapped[float] = mapped_column(Float, nullable=False)  # 0-100

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return (
            f"<FinalScore email={self.employee_email!r} "
            f"{self.year}/{self.month:02d} final={self.final_score:.2f}>"
        )
