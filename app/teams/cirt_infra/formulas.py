"""
app/teams/cirt_infra/formulas.py
─────────────────────────────────
Pure scoring formula functions for the CIRT & Infra Team evaluation.

All functions are stateless and free of I/O — they take numbers, return numbers.
Every formula is derived from the authoritative rules in
``documentation/Employee Performance Evaluation___.md`` (Section 4).

Score structure reference (max 100):
  ┌─ Segment A: Functional Activity             max 30
  │   log_hours_score         = tiered normalisation of CRM log hours (0-100)
  │   monthly_functional      = log_hours_score * 0.9 + sentiment_score * 0.1
  │   segment_a_marks         = monthly_functional * 0.3
  │
  ├─ Segment B: Office Discipline & Leadership max 50
  │   attendance_marks        = attendance_score / 10      (0-10)
  │   tl_marks                = support_readiness + kpi + general  (0-40)
  │   segment_b_marks         = attendance_marks + tl_marks
  │
  └─ Final: (base_total / 80) * 100    (NO reward marks for CIRT/Infra)
"""

from __future__ import annotations

# ─────────────────────────────────────────────────────────────────────────────
# CRM Log Hour Normalisation  (docs §4.1, Step 1)
# ─────────────────────────────────────────────────────────────────────────────


def normalise_cirt_log_hours(hours: float) -> float:
    """
    Convert raw log hours into a 0-100 score using CIRT-team specific tiers.

    Reference: documentation/Employee Performance Evaluation___.md  §4.1, Step 1.

    Thresholds (strictly greater-or-equal except for the 0 bucket):
        >= 160  → 100
        >= 140  →  80
        >= 130  →  70
        >= 120  →  60
        >= 110  →  50
        >=  80  →  40
        ==   0  →   0
        else (0 < hours <= 80) → 20
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
# Functional Score (0-100)  (docs §4.1, Step 3)
# ─────────────────────────────────────────────────────────────────────────────


def compute_cirt_functional_score(
    log_hours: float,
    sentiment_score: float,
) -> float:
    """
    Compute the monthly functional score from log hours and sentiment.

    Formula:
        monthly_functional_score = (log_hours_score * 0.9) + (sentiment * 0.1)
    """
    log_hours_score = normalise_cirt_log_hours(log_hours)
    return round(log_hours_score * 0.9 + sentiment_score * 0.1, 2)


def compute_segment_a_marks(monthly_functional_score: float) -> float:
    """
    Scale the 0-100 functional score down to 0-30 marks (docs §4.1 Step 3).
    """
    return round(monthly_functional_score * 0.30, 2)


# ─────────────────────────────────────────────────────────────────────────────
# Segment B (0-50)  (docs §4.2)
# ─────────────────────────────────────────────────────────────────────────────


def compute_attendance_marks(attendance_score: float) -> float:
    """
    Scale attendance score 0-100 to attendance marks 0-10 (docs §4.2, item 1).
    """
    return round(attendance_score / 10.0, 2)


def compute_tl_total(
    support_readiness: float,
    kpi: float,
    general: float,
) -> float:
    """
    Sum TL assessment marks (docs §4.2, item 2).

    Max per component:
        support_readiness: 10
        kpi:               15
        general:           15
    Total max: 40
    """
    return round(support_readiness + kpi + general, 2)


def compute_segment_b_marks(
    attendance_marks: float,
    tl_total: float,
) -> float:
    """
    Combine attendance and TL marks for Segment B total (max 50).
    """
    return round(attendance_marks + tl_total, 2)


# ─────────────────────────────────────────────────────────────────────────────
# Final Score (0-100)  (docs §4.3)
# ─────────────────────────────────────────────────────────────────────────────


def compute_cirt_final_score(
    segment_a_marks: float,
    segment_b_marks: float,
) -> tuple[float, float]:
    """
    Compute base total and final score.

    CIRT & Infra teams do NOT have reward marks (docs §4.3).  The base
    total is computed on an 80-mark scale, then normalised to 100.

    Formula:
        base_total  = segment_a_marks + segment_b_marks   (max 80)
        final_score = (base_total / 80) * 100

    Returns:
        (base_total, final_score)
    """
    base_total = round(segment_a_marks + segment_b_marks, 2)
    final_score = round((base_total / 80.0) * 100.0, 2)
    return base_total, final_score
