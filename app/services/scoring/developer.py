"""
app/services/scoring/developer.py
───────────────────────────────────
Developer Performance Scoring Engine.

Implements the EXACT formulas from §Developer Performance Evaluation:

┌──────────────────────────────────────────────────────────────────────────────
│ SEGMENT A  (max 50 marks)
│
│   Component1 = quality_check          (AI code quality score, 0–100)
│   Component2 = work_log_score * 0.9 + sentiment_score * 0.1
│   segment_a  = (Component1 + Component2) / 2
│   segment_a_marks = segment_a / 2        (maps 0–100 → 0–50)
│
├──────────────────────────────────────────────────────────────────────────────
│ SEGMENT B  (max 55 marks)
│
│   attendance_marks  = attendance_score / 10   (0–100 → 0–10)
│   problem_solving   = TL score (0–10)
│   kpi               = TL score (0–15)
│   general           = TL score (0–15)
│   segment_b_marks   = attendance_marks + problem_solving + kpi + general
│                     max = 10 + 10 + 15 + 15 = 50  (base cap)
│
├──────────────────────────────────────────────────────────────────────────────
│ BASE TOTAL
│   base_total = segment_a_marks + segment_b_marks   (max 100)
│
├──────────────────────────────────────────────────────────────────────────────
│ REWARD SCORE  (max 5 marks)
│
│   raw = MIN(attendance_score + log_hour_score + tl_total + quality_check, 140)
│   reward = round((raw * 5) / 140, 2)
│
├──────────────────────────────────────────────────────────────────────────────
│ FINAL SCORE (0–100)
│
│   final_score = ((base_total + reward) / 105) * 100
│
├──────────────────────────────────────────────────────────────────────────────
│ WORK LOG NORMALISATION (hours → 0–100 score)
│   >= 160 h → 100
│   >= 140 h →  90
│   >= 120 h →  80
│   >= 100 h →  70
│   >=  80 h →  60
│   >=  60 h →  50
│   <   60 h →  40
└──────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging_config import get_logger
from app.models.employee import Employee
from app.models.scores import (
    AttendanceScore,
    CodeQualityScore,
    FinalScore,
    SentimentScore,
    TLAssessmentScore,
    WorkLogScore,
)
from app.repositories.score_repository import (
    AttendanceRepository,
    CodeQualityRepository,
    FinalScoreRepository,
    SentimentRepository,
    TLAssessmentRepository,
    WorkLogRepository,
)
from app.services.ai.sentiment import compute_employee_sentiment_score
from app.services.data_sources.commit_gitlab_client import CommitBasedGitLabClient
from app.services.data_sources.mysql_client import MySQLCRMClient, MySQLHRClient
from app.services.data_sources.postgresql_gitlab_client import PostgreSQLGitLabClient
from app.services.scoring.base import AbstractScorer
from app.services.workflows.commit_analysis import run_commit_analysis

logger = get_logger(__name__)


# ── Component 1 sub-score helpers ────────────────────────────────────────────


def normalise_lines_added(additions: int) -> float:
    """Tiered score for code lines added (0–100)."""
    if additions >= 3000:
        return 100.0
    elif additions >= 1500:
        return 85.0
    elif additions >= 750:
        return 70.0
    elif additions >= 300:
        return 55.0
    elif additions >= 150:
        return 40.0
    elif additions >= 1:
        return 25.0
    return 0.0


def normalise_lines_deleted(deletions: int) -> float:
    """Tiered score for code lines deleted/refactored (0–100)."""
    if deletions >= 1500:
        return 100.0
    elif deletions >= 750:
        return 85.0
    elif deletions >= 300:
        return 70.0
    elif deletions >= 150:
        return 55.0
    elif deletions >= 50:
        return 40.0
    elif deletions >= 1:
        return 25.0
    return 0.0


def compute_component1(
    code_quality: float,
    resolution_rate: float,
    reopen_quality: float,
    lines_added_score: float,
    lines_deleted_score: float,
) -> float:
    """
    Weighted Component 1 (0–100):
        Code Quality       30%
        Resolution Rate    35%
        Reopen Quality     15%
        Lines Added        10%
        Lines Deleted      10%
    """
    return round(
        code_quality * 0.30
        + resolution_rate * 0.35
        + reopen_quality * 0.15
        + lines_added_score * 0.10
        + lines_deleted_score * 0.10,
        4,
    )


# ── Work-log normalisation ────────────────────────────────────────────────────


def normalise_work_hours(hours: float) -> float:
    """Map raw worked hours to a 0–100 score (step function from docs)."""
    if hours >= 160:
        return 100.0
    elif hours >= 140:
        return 90.0
    elif hours >= 120:
        return 80.0
    elif hours >= 100:
        return 70.0
    elif hours >= 80:
        return 60.0
    elif hours >= 60:
        return 50.0
    else:
        return 40.0


# ── Formula helpers ───────────────────────────────────────────────────────────


def compute_segment_a(
    quality_check: float,
    work_log_score: float,
    sentiment_score: float,
) -> tuple[float, float]:
    """
    Returns (segment_a_raw, segment_a_marks).
    segment_a_marks = segment_a_raw / 2  (normalises 0–100 → 0–50)
    """
    component2 = work_log_score * 0.9 + sentiment_score * 0.1
    segment_a = (quality_check + component2) / 2
    segment_a_marks = segment_a / 2
    return round(segment_a, 4), round(segment_a_marks, 4)


def compute_segment_b(
    attendance_score: float,
    problem_solving: float,
    kpi: float,
    general: float,
) -> float:
    """attendance_marks + TL scores (max 10+10+15+15 = 50)."""
    attendance_marks = attendance_score / 10
    return round(attendance_marks + problem_solving + kpi + general, 4)


def compute_reward(
    attendance_score: float,
    log_hour_score: float,
    tl_total: float,
    quality_check: float,
) -> float:
    """
    reward = (MIN(sum, 140) * 5) / 140
    tl_total = problem_solving + kpi + general (from TL assessment)
    """
    raw = min(attendance_score + log_hour_score + tl_total + quality_check, 140.0)
    return round((raw * 5) / 140, 2)


def compute_final_score(base_total: float, reward: float) -> float:
    """final_score = ((base_total + reward) / 105) * 100"""
    return round(((base_total + reward) / 105) * 100, 2)


# ── Developer Scorer ──────────────────────────────────────────────────────────


class DeveloperScorer(AbstractScorer):
    """
    Concrete scorer for the Developer team.

    Data flow per employee:
        1. Run LangGraph MR analysis  → quality_check score
        2. Query MySQL CRM            → work_log_hours, sentiment descriptions
        3. Query MySQL HR             → attendance_score
        4. Load uploaded TL scores    → problem_solving, kpi, general
        5. Apply formulas             → segment_a, segment_b, reward, final
        6. Persist all score rows     → SQLite
    """

    async def calculate(
        self,
        employee: Employee,
        evaluation_run_id: int,
        year: int,
        month: int,
        db: AsyncSession,
    ) -> dict:
        log = logger.bind(
            employee=employee.employee_id,
            year=year,
            month=month,
        )
        log.info("developer_scoring_start")

        result: dict = {
            "employee_id": employee.employee_id,
            "final_score": 0.0,
            "segment_a_marks": 0.0,
            "segment_b_marks": 0.0,
            "base_total": 0.0,
            "reward_score": 0.0,
            "error": None,
        }

        try:
            # ── 1. Code quality via commit-based LangGraph analysis ───────────
            commit_state = await run_commit_analysis(
                employee_email=employee.email,
                gitlab_username=employee.gitlab_username or employee.employee_id,
                author_email=employee.email,
                evaluation_run_id=evaluation_run_id,
                year=year,
                month=month,
            )
            code_quality_ai: float = commit_state["aggregate_score"]

            # ── 1.5  Fetch issue stats + line stats for Component 1 ───────────
            gitlab_username = employee.gitlab_username or employee.employee_id

            pg_client = PostgreSQLGitLabClient()
            commit_client = CommitBasedGitLabClient()
            try:
                issue_stats = await pg_client.get_issue_stats(
                    user_email=employee.email,
                    year=year,
                    month=month,
                )
                line_stats = await commit_client.get_developer_line_stats(
                    username=gitlab_username,
                    author_email=employee.email,
                    year=year,
                    month=month,
                )
            finally:
                await pg_client.close()
                await commit_client.close()

            total_assigned: int = issue_stats["total_assigned"]
            total_resolved: int = issue_stats["total_resolved"]
            total_reopens: int = issue_stats["total_reopens"]

            # Resolution rate (0-100)
            resolution_rate: float = (
                (total_resolved / total_assigned * 100) if total_assigned > 0 else 0.0
            )

            # Reopen quality (0-100): fewer reopens → higher score
            reopen_rate: float = (
                (total_reopens / total_assigned * 100) if total_assigned > 0 else 0.0
            )
            reopen_quality: float = max(0.0, min(100.0, 100.0 - reopen_rate))

            # Line tier scores (0-100)
            lines_added_score: float = normalise_lines_added(
                line_stats["total_additions"]
            )
            lines_deleted_score: float = normalise_lines_deleted(
                line_stats["total_deletions"]
            )

            # Weighted Component 1
            quality_check: float = compute_component1(
                code_quality=code_quality_ai,
                resolution_rate=resolution_rate,
                reopen_quality=reopen_quality,
                lines_added_score=lines_added_score,
                lines_deleted_score=lines_deleted_score,
            )

            log.info(
                "component1_computed",
                code_quality=code_quality_ai,
                resolution_rate=round(resolution_rate, 2),
                reopen_quality=round(reopen_quality, 2),
                lines_added_score=lines_added_score,
                lines_deleted_score=lines_deleted_score,
                component1=quality_check,
            )

            # Persist each commit bundle score row
            cq_repo = CodeQualityRepository(db)
            for row in commit_state["mr_scores"]:
                await cq_repo.create(
                    CodeQualityScore(
                        evaluation_run_id=row["evaluation_run_id"],
                        employee_email=row["employee_email"],
                        mr_reference=row["mr_reference"],
                        mr_title=row["mr_title"],
                        raw_score=row["raw_score"],
                        readability_score=row["readability_score"],
                        logic_efficiency_score=row["logic_efficiency_score"],
                        error_handling_score=row["error_handling_score"],
                        architecture_score=row["architecture_score"],
                        security_score=row["security_score"],
                        reasoning=row["reasoning"],
                        issues=row["issues"],
                        model_used=row["model_used"],
                    )
                )

            # ── 2. Work logs from CRM MySQL ───────────────────────────────────
            crm = MySQLCRMClient()
            log_records = await crm.get_developer_work_logs(
                employee_ids=[employee.employee_id],
                year=year,
                month=month,
            )
            descriptions = await crm.get_developer_log_descriptions(
                employee_ids=[employee.employee_id],
                year=year,
                month=month,
            )

            # Sum hours for this employee
            total_hours = sum(
                float(row.get("total_hours", 0))
                for row in log_records
                if str(row.get("employee_id", "")) == employee.employee_id
            )
            work_log_score = normalise_work_hours(total_hours)

            # Persist work log score
            wl_repo = WorkLogRepository(db)
            await wl_repo.create(
                WorkLogScore(
                    evaluation_run_id=evaluation_run_id,
                    employee_email=employee.email,
                    total_log_hours=total_hours,
                    normalized_score=work_log_score,
                    year=year,
                    month=month,
                )
            )

            # ── 3. Sentiment from log descriptions ────────────────────────────
            desc_texts = [
                str(row.get("description", ""))
                for row in descriptions
                if str(row.get("employee_id", "")) == employee.employee_id
            ]
            sentiment_avg, avg_polarity = compute_employee_sentiment_score(desc_texts)

            sent_repo = SentimentRepository(db)
            await sent_repo.create(
                SentimentScore(
                    evaluation_run_id=evaluation_run_id,
                    employee_email=employee.email,
                    score=sentiment_avg,
                    average_polarity=avg_polarity,
                    total_logs_analyzed=len(desc_texts),
                    year=year,
                    month=month,
                )
            )

            # ── 4. Attendance from HR MySQL ───────────────────────────────────
            hr = MySQLHRClient()
            attendance_records = await hr.get_attendance(
                employee_ids=[employee.employee_id],
                year=year,
                month=month,
            )
            attendance_row = next(
                (
                    r
                    for r in attendance_records
                    if str(r.get("employee_id", "")) == employee.employee_id
                ),
                None,
            )

            if attendance_row:
                attendance_score = float(attendance_row.get("attendance_score", 60.0))
                present = int(attendance_row.get("present", 0))
                late = int(attendance_row.get("late", 0))
                actual_work_days = int(attendance_row.get("actual_work_days", 22))
            else:
                attendance_score = 60.0
                present = 0
                late = 0
                actual_work_days = 22

            att_repo = AttendanceRepository(db)
            await att_repo.create(
                AttendanceScore(
                    evaluation_run_id=evaluation_run_id,
                    employee_email=employee.email,
                    present_days=present,
                    late_attendance=late,
                    work_days=actual_work_days,
                    actual_work_days=actual_work_days,
                    late_days=late // 3,
                    score=attendance_score,
                    year=year,
                    month=month,
                )
            )

            # ── 5. TL Assessment scores ───────────────────────────────────────
            tl_repo = TLAssessmentRepository(db)
            tl_score = await tl_repo.get_for_employee_period(
                employee_email=employee.email,
                year=year,
                month=month,
                evaluation_run_id=evaluation_run_id,
            )

            if tl_score:
                problem_solving = float(tl_score.problem_solving)
                kpi = float(tl_score.kpi)
                general = float(tl_score.general)
            else:
                log.warning("tl_score_not_found_using_defaults")
                problem_solving = 5.0  # mid-range defaults
                kpi = 7.5
                general = 7.5

            tl_total = problem_solving + kpi + general

            # ── 6. Apply formulas ─────────────────────────────────────────────
            _, segment_a_marks = compute_segment_a(
                quality_check=quality_check,
                work_log_score=work_log_score,
                sentiment_score=sentiment_avg,
            )
            segment_b_marks = compute_segment_b(
                attendance_score=attendance_score,
                problem_solving=problem_solving,
                kpi=kpi,
                general=general,
            )
            base_total = round(segment_a_marks + segment_b_marks, 4)
            reward = compute_reward(
                attendance_score=attendance_score,
                log_hour_score=work_log_score,
                tl_total=tl_total,
                quality_check=quality_check,
            )
            final = compute_final_score(base_total, reward)

            # ── 7. Persist final score ────────────────────────────────────────
            fs_repo = FinalScoreRepository(db)
            await fs_repo.create(
                FinalScore(
                    evaluation_run_id=evaluation_run_id,
                    employee_email=employee.email,
                    quality_check_score=code_quality_ai,
                    resolution_rate=round(resolution_rate, 4),
                    reopen_quality_score=round(reopen_quality, 4),
                    lines_added_score=lines_added_score,
                    lines_deleted_score=lines_deleted_score,
                    component1_score=quality_check,
                    component2_score=work_log_score,
                    segment_a_score=round((quality_check + work_log_score) / 2, 4),
                    segment_a_marks=segment_a_marks,
                    attendance_score=attendance_score,
                    attendance_marks=round(attendance_score / 10, 4),
                    problem_solving=problem_solving,
                    kpi=kpi,
                    general_assessment=general,
                    tl_total=tl_total,
                    segment_b_marks=segment_b_marks,
                    base_total=base_total,
                    reward_score=reward,
                    final_score=final,
                    year=year,
                    month=month,
                )
            )

            result.update(
                {
                    "final_score": final,
                    "segment_a_marks": segment_a_marks,
                    "segment_b_marks": segment_b_marks,
                    "base_total": base_total,
                    "reward_score": reward,
                    # Component 1 breakdown
                    "component1_score": quality_check,
                    "code_quality_ai": code_quality_ai,
                    "resolution_rate": round(resolution_rate, 4),
                    "reopen_quality_score": round(reopen_quality, 4),
                    "lines_added_score": lines_added_score,
                    "lines_deleted_score": lines_deleted_score,
                    # Other scores
                    "work_log_score": work_log_score,
                    "sentiment_score": sentiment_avg,
                    "attendance_score": attendance_score,
                    "problem_solving": problem_solving,
                    "kpi": kpi,
                    "general": general,
                }
            )
            log.info("developer_scoring_complete", final_score=final)

        except Exception as exc:
            log.error("developer_scoring_failed", error=str(exc))
            result["error"] = str(exc)

        return result
