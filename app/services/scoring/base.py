"""
app/services/scoring/base.py
──────────────────────────────
Abstract base class for all team scorer implementations.

Each team (Developer, Support, QA, etc.) will have its own concrete
scorer that inherits from AbstractScorer and implements the calculate()
method with team-specific formulas.
"""

from __future__ import annotations

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
        # "sqa": ("app.services.scoring.sqa", "SQAScorer"),
        # "support": ("app.services.scoring.support", "SupportScorer"),
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
