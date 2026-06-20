"""
app/teams/gsd/formulas.py
─────────────────────────
Pure scoring formula functions for GSD Team evaluation.

All functions are stateless and free of I/O — they take numbers, return numbers.
Every formula is derived from the authoritative rules in:
  documentation/Employee Performance Evaluation___.md  (Section 7)

Score structure reference (max 100):
  ┌─ Segment A: Functional Performance          max 30
  │   monthly_functional = crm*0.8 + tickets*0.2  (0-100)
  │   segment_a_marks    = monthly_functional * 0.3
  │
  ├─ Segment B: Discipline & Leadership         max 50
  │   attendance_marks   = attendance_score / 10   (0-10)
  │   tl_marks           = readiness + kpi + gen   (0-40)
  │   segment_b_marks    = attendance + tl
  │
  └─ Final: (base_total / 80) * 100    (no reward marks for GSD teams)
"""

from __future__ import annotations

# ─────────────────────────────────────────────────────────────────────────────
# CRM Log Hour Normalisation (GSD-specific tiers)
# ─────────────────────────────────────────────────────────────────────────────


def normalise_gsd_log_hours(hours: float) -> float:
    """
    Convert raw log hours into a 0-100 score using GSD-specific tiers.

    Reference: Section 7 — GSD Team Evaluation.
    Exact thresholds (inclusive):
        >= 120 → 100
        >= 110 →  80
        >= 100 →  70
        >=  90 →  60
        >=  80 →  50
        >=  50 →  40
        ==   0 →   0
        else   →  20   (0 < hours < 50)
    """
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


# ─────────────────────────────────────────────────────────────────────────────
# CRM Log Score (0-100)
# ─────────────────────────────────────────────────────────────────────────────


def compute_crm_log_score(
    log_hours: float,
    sentiment_score: float,
) -> tuple[float, float]:
    """
    Compute the final CRM log score from normalised hours and sentiment.

    Args:
        log_hours:      Total raw log hours for the month.
        sentiment_score: Averaged TextBlob score 0-100.

    Returns:
        (log_hours_score, crm_log_score)
          log_hours_score: tiered normalised hours (0-100)
          crm_log_score:   weighted composite (0-100)
    """
    log_hours_score = normalise_gsd_log_hours(log_hours)
    crm_log_score = round(log_hours_score * 0.9 + sentiment_score * 0.1, 2)
    return log_hours_score, crm_log_score


# ─────────────────────────────────────────────────────────────────────────────
# Ticket Scoring (0-100 each)
# ─────────────────────────────────────────────────────────────────────────────


def compute_monthly_tickets_score(total_tickets: int) -> float:
    if total_tickets >= 30:
        return 100.0
    elif total_tickets >= 20:
        return 80.0
    elif total_tickets >= 10:
        return 70.0
    elif total_tickets > 0:
        return 60.0
    elif total_tickets == 0:
        return 40.0
    else:
        return 50.0


def compute_ticket_resolution_score(average_taken_days: float) -> float:
    return 100.0 if average_taken_days <= 2.0 else 60.0


def compute_tickets_evaluation_score(
    total_tickets: int,
    average_taken_days: float,
) -> tuple[float, float, float]:
    volume_score = compute_monthly_tickets_score(total_tickets)
    speed_score = compute_ticket_resolution_score(average_taken_days)
    tickets_eval = round(volume_score * 0.7 + speed_score * 0.3, 2)
    return volume_score, speed_score, tickets_eval


# ─────────────────────────────────────────────────────────────────────────────
# Functional Score (0-100) → Segment A (0-30)
# ─────────────────────────────────────────────────────────────────────────────


def compute_functional_score(
    crm_log_score: float,
    tickets_evaluation_score: float,
) -> float:
    return round(crm_log_score * 0.8 + tickets_evaluation_score * 0.2, 2)


def compute_segment_a_marks(monthly_functional_score: float) -> float:
    return round(monthly_functional_score * 0.30, 2)


# ─────────────────────────────────────────────────────────────────────────────
# Segment B (0-50)
# ─────────────────────────────────────────────────────────────────────────────


def compute_attendance_marks(attendance_score: float) -> float:
    return round(attendance_score / 10.0, 2)


def compute_tl_total(
    support_readiness: float,
    kpi: float,
    general: float,
) -> float:
    return round(support_readiness + kpi + general, 2)


def compute_segment_b_marks(
    attendance_marks: float,
    tl_total: float,
) -> float:
    return round(attendance_marks + tl_total, 2)


# ─────────────────────────────────────────────────────────────────────────────
# Final Score (0-100)
# ─────────────────────────────────────────────────────────────────────────────


def compute_gsd_final_score(
    segment_a_marks: float,
    segment_b_marks: float,
) -> tuple[float, float]:
    base_total = round(segment_a_marks + segment_b_marks, 2)
    final_score = round((base_total / 80.0) * 100.0, 2)
    return base_total, final_score
