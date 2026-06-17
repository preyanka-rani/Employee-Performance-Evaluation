from __future__ import annotations


def normalise_hr_log_hours(hours: float) -> float:
    if hours >= 160:
        return 100.0
    elif hours >= 140:
        return 80.0
    elif hours >= 130:
        return 70.0
    elif hours >= 120:
        return 60.0
    elif hours >= 110:
        return 50.0
    elif hours >= 80:
        return 40.0
    elif hours == 0:
        return 0.0
    else:
        return 20.0


def compute_hr_functional_score(
    log_hours: float,
    sentiment_score: float,
) -> tuple[float, float]:
    log_hours_score = normalise_hr_log_hours(log_hours)
    monthly_functional_score = round(log_hours_score * 0.9 + sentiment_score * 0.1, 2)
    return log_hours_score, monthly_functional_score


def compute_segment_a_marks(monthly_functional_score: float) -> float:
    return round(monthly_functional_score * 0.30, 2)


def compute_attendance_marks(attendance_score: float) -> float:
    return round(attendance_score / 10.0, 2)


def compute_tl_total(
    problem_solving: float,
    kpi: float,
    general: float,
) -> float:
    return round(problem_solving + kpi + general, 2)


def compute_segment_b_marks(
    attendance_marks: float,
    tl_total: float,
) -> float:
    return round(attendance_marks + tl_total, 2)


def compute_hr_final_score(
    segment_a_marks: float,
    segment_b_marks: float,
) -> tuple[float, float]:
    base_total = round(segment_a_marks + segment_b_marks, 2)
    final_score = round((base_total / 80.0) * 100.0, 2)
    return base_total, final_score
