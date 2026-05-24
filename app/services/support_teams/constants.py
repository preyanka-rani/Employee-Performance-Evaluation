"""
app/services/support_teams/constants.py
────────────────────────────────────────
Static configuration for the Support Teams evaluation module.

Teams covered:
  - impl_its        : Implementation & ITS
  - onsite_support  : Onsite Support
  - production      : Production
  - tech_support    : Tech Support

All four teams share the same scoring logic and data sources;
they differ only in which employees belong to each sub-team.
Employee lists here match the documentation SQL IN clauses.
They serve as a fallback when the DB lookup returns nothing.
"""

from __future__ import annotations

# All support team employee IDs from documentation (combined list)
# Reference: perform_crm.sql and tickets_score.sql IN clauses
ALL_SUPPORT_EMPLOYEE_IDS: list[str] = [
    "20240006",
    "20050002",
    "20240039",
    "20220020",
    "20160009",
    "20230026",
    "20240042",
    "20240018",
    "20220018",
    "20240023",
    "20240034",
    "20240037",
    "20220002",
    "20240025",
    "20240028",
    "20240010",
    "20230008",
    "20150002",
    "20150004",
    "20230003",
    "20240021",
    "20240020",
]

# Canonical set of team name keys recognised by the scorer factory.
# All map to SupportTeamScorer with the same scoring logic.
SUPPORT_TEAM_KEYS: frozenset[str] = frozenset(
    {
        "impl_its",
        "onsite_support",
        "production",
        "tech_support",
        # Convenience aliases accepted at the API layer
        "support",
        "implementation",
        "implementation_its",
    }
)

# ── CRM Log Hour Normalisation (support teams) ────────────────────────────────
# Reference: funcational_log_activities.py  transform() function
# Thresholds use strictly-greater-than (>) comparisons.
LOG_HOUR_TIERS: list[tuple[float, float]] = [
    (160.0, 100.0),  # > 160 → 100
    (140.0, 80.0),  # > 140 → 80
    (130.0, 70.0),  # > 130 → 70
    (120.0, 60.0),  # > 120 → 60
    (110.0, 50.0),  # > 110 → 50
    (80.0, 40.0),  # >  80 → 40
]
# hours == 0 → 0;  0 < hours <= 80 → 20
LOG_HOUR_ZERO_SCORE: float = 0.0
LOG_HOUR_FLOOR_SCORE: float = 20.0  # score for 0 < hours <= 80

# ── Ticket Volume Tiers ───────────────────────────────────────────────────────
# Reference: tickets_score.sql
TICKET_TIERS: list[tuple[int, float]] = [
    (30, 100.0),
    (20, 80.0),
    (10, 70.0),
    (1, 60.0),
]
TICKET_ZERO_SCORE: float = 40.0  # score when total_tickets == 0

# Average ticket resolution: ≤2 days → 100, else → 60
TICKET_FAST_DAYS: float = 2.0
TICKET_FAST_SCORE: float = 100.0
TICKET_SLOW_SCORE: float = 60.0

# ── Weights ───────────────────────────────────────────────────────────────────
CRM_LOG_WEIGHT: float = 0.8  # in functional score
TICKETS_WEIGHT: float = 0.2  # in functional score
LOG_HOURS_WEIGHT: float = 0.9  # in CRM log score
SENTIMENT_WEIGHT: float = 0.1  # in CRM log score
TICKETS_VOLUME_WEIGHT: float = 0.7  # in tickets evaluation score
TICKETS_SPEED_WEIGHT: float = 0.3  # in tickets evaluation score

# ── Score Scaling ─────────────────────────────────────────────────────────────
SEGMENT_A_MAX_MARKS: float = 30.0  # Functional → 30 marks
ATTENDANCE_MAX_MARKS: float = 10.0  # Office Discipline → 10 marks
TL_MAX_MARKS: float = 40.0  # TL Assessment → 40 marks
BASE_TOTAL_MAX: float = 80.0  # Base total before normalization
