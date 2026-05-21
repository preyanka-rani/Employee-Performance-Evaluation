"""
app/services/scoring/base.py
──────────────────────────────
Abstract base class for all team scorer implementations.

Each team (Developer, Support, QA, etc.) will have its own concrete
scorer that inherits from AbstractScorer and implements the calculate()
method with team-specific formulas.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.employee import Employee


class AbstractScorer(ABC):
    """
    Base class for all performance scorers.

    Concrete scorers must implement `calculate()` to perform:
    1. Data collection from all sources (GitLab, MySQL, Excel uploads)
    2. Score computation using team-specific formulas
    3. Persistence of all intermediate and final scores
    """

    @abstractmethod
    async def calculate(
        self,
        employee: Employee,
        evaluation_run_id: int,
        year: int,
        month: int,
        db: AsyncSession,
    ) -> dict:
        """
        Run the complete scoring pipeline for one employee.

        Args:
            employee:           SQLAlchemy Employee ORM instance.
            evaluation_run_id:  FK to the parent EvaluationRun row.
            year:               Calendar year of the evaluation period.
            month:              Calendar month (1-12) of the period.
            db:                 Async database session for read/write ops.

        Returns:
            A dict with at minimum:
                {
                  "employee_id": str,
                  "final_score": float,
                  "segment_a_marks": float,
                  "segment_b_marks": float,
                  "base_total": float,
                  "reward_score": float,
                  "error": None | str,
                }
        """
        ...


# ── Scorer factory (Open/Closed: add new teams without touching existing code) ─


def get_scorer(team: str) -> "AbstractScorer":
    """
    Return the concrete scorer instance for the given team name.

    Adding a new team (e.g. "SQA"):
        1. Create app/services/scoring/sqa.py with class SQAScorer(AbstractScorer).
        2. Register it in _SCORER_REGISTRY below — no other change needed.

    Raises:
        ValueError: If the team name has no registered scorer.
    """
    import importlib

    team_key = team.strip().lower()

    _SCORER_REGISTRY: dict[str, tuple[str, str]] = {
        "developer": ("app.services.scoring.developer", "DeveloperScorer"),
        # Support Teams (all four sub-teams share the same scorer)
        "impl_its": (
            "app.services.support_teams.scoring.support_scorer",
            "SupportTeamScorer",
        ),
        "onsite_support": (
            "app.services.support_teams.scoring.support_scorer",
            "SupportTeamScorer",
        ),
        "production": (
            "app.services.support_teams.scoring.support_scorer",
            "SupportTeamScorer",
        ),
        "tech_support": (
            "app.services.support_teams.scoring.support_scorer",
            "SupportTeamScorer",
        ),
        "support": (
            "app.services.support_teams.scoring.support_scorer",
            "SupportTeamScorer",
        ),
    }

    entry = _SCORER_REGISTRY.get(team_key)
    if entry is None:
        raise ValueError(
            f"No scorer registered for team '{team}'. "
            f"Available: {sorted(_SCORER_REGISTRY)}"
        )

    module_path, class_name = entry
    module = importlib.import_module(module_path)
    scorer_cls = getattr(module, class_name)
    return scorer_cls()


def resolve_team_key(raw_team: str) -> str:
    """
    Normalise a free-form team name to a registered scorer key.

    Resolution order:
        1. Strip + lowercase + collapse special characters → try exact match.
        2. Fuzzy substring matching against known key patterns.
        3. Raise ValueError if no match found.

    Examples:
        "tech_support"          → "tech_support"
        " Tech Support "        → "tech_support"
        "Implementation & ITS"  → "impl_its"
        "Onsite Support Team"   → "onsite_support"
        "Production"            → "production"
        "Developer"             → "developer"

    Raises:
        ValueError: If the team name cannot be mapped to any known key.
    """
    import importlib  # noqa: PLC0415

    # ── Step 1: normalise ─────────────────────────────────────────────────────
    normalised = re.sub(r"[\s\-&./,]+", "_", raw_team.strip().lower())
    normalised = re.sub(r"_+", "_", normalised).strip("_")

    _SCORER_REGISTRY_INNER: dict[str, tuple[str, str]] = {
        "developer": ("app.services.scoring.developer", "DeveloperScorer"),
        "impl_its": (
            "app.services.support_teams.scoring.support_scorer",
            "SupportTeamScorer",
        ),
        "onsite_support": (
            "app.services.support_teams.scoring.support_scorer",
            "SupportTeamScorer",
        ),
        "production": (
            "app.services.support_teams.scoring.support_scorer",
            "SupportTeamScorer",
        ),
        "tech_support": (
            "app.services.support_teams.scoring.support_scorer",
            "SupportTeamScorer",
        ),
        "support": (
            "app.services.support_teams.scoring.support_scorer",
            "SupportTeamScorer",
        ),
    }

    if normalised in _SCORER_REGISTRY_INNER:
        return normalised

    # ── Step 2: fuzzy pattern matching ────────────────────────────────────────
    # Order matters: more specific patterns first.
    _FUZZY_PATTERNS: list[tuple[str, str]] = [
        ("implementation", "impl_its"),
        ("impl_its", "impl_its"),
        ("impl", "impl_its"),
        ("i_t_s", "impl_its"),
        ("its", "impl_its"),
        ("onsite", "onsite_support"),
        ("on_site", "onsite_support"),
        ("production", "production"),
        ("tech_support", "tech_support"),
        ("technical_support", "tech_support"),
        ("tech", "tech_support"),
        ("developer", "developer"),
        ("development", "developer"),
        ("dev", "developer"),
        ("support", "support"),  # most generic – last
    ]

    for pattern, key in _FUZZY_PATTERNS:
        if pattern in normalised:
            from app.core.logging_config import get_logger  # noqa: PLC0415

            get_logger(__name__).warning(
                "team_key_fuzzy_matched",
                raw_team=raw_team,
                normalised=normalised,
                resolved_key=key,
            )
            return key

    raise ValueError(
        f"Cannot resolve team name '{raw_team}' to a known scorer. "
        f"Please use one of: {sorted(_SCORER_REGISTRY_INNER)}"
    )
