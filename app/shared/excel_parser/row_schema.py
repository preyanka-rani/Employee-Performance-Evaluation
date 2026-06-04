"""
app/shared/excel_parser/row_schema.py
─────────────────────────────────────
Canonical TL row schema — the unified data contract used by every team.

A CanonicalRow is what every worker graph receives as input. Team-specific
fields (e.g. "support_readiness" vs "problem_solving") are normalised here so
downstream workers never need to know which Excel header the user uploaded.

Design intent
─────────────
- **One schema, every team**: the parser populates every relevant field;
  team workers read only what they need.
- **No team-specific logic in this file**: this is the contract, not the
  business rules. Validation is intentionally minimal; teams enforce their
  own bounds inside their own graph nodes if they need stricter rules.

Field reference
───────────────
employee_id    : str  – internal HR ID; may be empty (resolved later by MySQL)
employee_email : str  – work email (lowercased, required, must contain '@')
employee_name  : str  – full name (required)
problem_solving: float 0–10  (developer TL score, "Critical Thinking & PS")
support_readiness: float 0–10  (support team TL score, alias of PS)
kpi           : float 0–15
general       : float 0–15
gitlab_username: str | None
team_name     : str – raw team label from the Excel (if a team column exists)
"""

from __future__ import annotations

from dataclasses import dataclass, field


# Fields the parser ALWAYS must resolve — independent of team.
REQUIRED_FIELDS: frozenset[str] = frozenset(
    {"employee_email", "problem_solving_or_readiness", "kpi", "general"}
)


@dataclass
class CanonicalRow:
    """
    One validated TL assessment row, normalised across all teams.

    Either ``problem_solving`` OR ``support_readiness`` is populated depending
    on the source Excel's column naming. Both default to 0.0 so a downstream
    worker can sum ``ps_or_readiness + kpi + general`` without conditional
    logic.
    """

    employee_id: str = ""
    employee_email: str = ""
    employee_name: str = ""
    problem_solving: float = 0.0
    support_readiness: float = 0.0
    kpi: float = 0.0
    general: float = 0.0
    gitlab_username: str | None = None
    team_name: str = ""

    @property
    def ps_or_readiness(self) -> float:
        """Unified PS-or-Readiness value: prefer support_readiness if set."""
        return self.support_readiness if self.support_readiness else self.problem_solving

    @property
    def tl_total(self) -> float:
        return round(self.problem_solving + self.kpi + self.general, 4)

    @property
    def support_tl_total(self) -> float:
        return round(self.support_readiness + self.kpi + self.general, 4)
