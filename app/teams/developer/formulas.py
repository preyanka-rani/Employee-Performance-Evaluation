"""
app/teams/developer/formulas.py
───────────────────────────────
Developer scoring formulas — moved byte-for-byte from
app/services/scoring/developer.py to preserve 100% functional parity.

These are pure functions (no I/O, no side effects). They are invoked by
the developer LangGraph nodes; nothing in this file has been changed.
"""

from __future__ import annotations

# ── Component 1 sub-score helpers ─────────────────────────────────────────────


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


# ── Work-log normalisation ───────────────────────────────────────────────────


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

def calculate_evaluation_grade(pct_value: float) -> str:
    """
    Calculate the evaluation grade based on the percentage score.
    Rules: 88-100=A, 84-87=B, 75-83=C, 0-74=D
    """
    if pct_value >= 88:
        return 'A'
    if pct_value >= 84:
        return 'B'
    if pct_value >= 75:
        return 'C'
    return 'D'
