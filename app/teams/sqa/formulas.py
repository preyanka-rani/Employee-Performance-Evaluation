"""
app/teams/sqa/formulas.py
─────────────────────────
SQA scoring formulas — adapted from the developer formulas.

Segment A: Component 1 (code quality) + Component 2 (work logs + sentiment).
           segment_a_marks out of 30 instead of 50.
No reward marks.
Final score = (base_total / 80) * 100.
"""

from __future__ import annotations


def normalise_lines_added(additions: int) -> float:
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
    return round(
        code_quality * 0.30
        + resolution_rate * 0.35
        + reopen_quality * 0.15
        + lines_added_score * 0.10
        + lines_deleted_score * 0.10,
        4,
    )


def normalise_work_hours(hours: float) -> float:
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


def compute_segment_a(
    quality_check: float,
    work_log_score: float,
    sentiment_score: float,
) -> tuple[float, float]:
    """
    Returns (segment_a_raw, segment_a_marks).
    segment_a_marks = segment_a_raw * 0.30 (normalises 0-100 to 0-30)
    """
    component2 = work_log_score * 0.9 + sentiment_score * 0.1
    segment_a = (quality_check + component2) / 2
    segment_a_marks = segment_a * 0.30
    return round(segment_a, 4), round(segment_a_marks, 4)


def compute_segment_b(
    attendance_score: float,
    problem_solving: float,
    kpi: float,
    general: float,
) -> float:
    attendance_marks = attendance_score / 10
    return round(attendance_marks + problem_solving + kpi + general, 4)


def compute_sqa_final_score(
    segment_a_marks: float,
    segment_b_marks: float,
) -> tuple[float, float]:
    """
    SQA has no reward marks.
    base_total = segment_a_marks + segment_b_marks (max 30 + 50 = 80)
    final_score = (base_total / 80) * 100
    """
    base_total = round(segment_a_marks + segment_b_marks, 4)
    final_score = round((base_total / 80.0) * 100.0, 2)
    return base_total, final_score


def calculate_evaluation_grade(pct_value: float) -> str:
    if pct_value >= 88:
        return "A"
    if pct_value >= 84:
        return "B"
    if pct_value >= 75:
        return "C"
    return "D"
