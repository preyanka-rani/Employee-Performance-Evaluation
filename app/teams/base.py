"""
app/teams/base.py
─────────────────
TeamContract — the abstract base class every team must implement.

A TeamContract binds together:
  - team_key, display_name — identifiers
  - compiled_graph          — a LangGraph StateGraph (run per-employee by
                              the orchestrator) OR a callable that the
                              orchestrator invokes for the whole team.
  - generate_report         — builds the team's Excel report.

Two execution methods are supported, picked by the orchestrator:
  - run_per_employee  : for teams that score one employee at a time
                        (developer, support — the common case)
  - run_bulk         : for teams that batch the whole team in one call
                        (future use; not used by current teams)

For now every team implements run_per_employee. The orchestrator loops
over the parsed rows and invokes it once per row, collecting results.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, ClassVar

from langgraph.graph import StateGraph
from sqlalchemy.ext.asyncio import AsyncSession

from app.shared.excel_parser.row_schema import CanonicalRow


class TeamContext(dict):
    """
    Per-invocation context passed to a team's ``run_per_employee``.

    Contains the parent EvaluationRun id, year, month, db session, and
    the team's own key. Teams may add their own pre-fetched bulk data
    (e.g. team-wide MySQL attendance rows) via ``ctx["extra"]``.
    """

    run_id: int
    team_key: str
    year: int
    month: int
    db: AsyncSession
    team_display_name: str

    def __init__(  # noqa: D107
        self,
        *,
        run_id: int,
        team_key: str,
        year: int,
        month: int,
        db: AsyncSession,
        team_display_name: str = "",
        extra: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            run_id=run_id,
            team_key=team_key,
            year=year,
            month=month,
            db=db,
            team_display_name=team_display_name,
            extra=extra or {},
        )


class TeamContract(ABC):
    """
    Abstract base for every team.

    Concrete subclasses are typically *stateless* — they only carry
    class-level config and a compiled LangGraph. The orchestrator
    instantiates them via ``cls()`` once at startup and reuses the
    instance for every request.
    """

    # ── Class-level configuration (override in subclasses) ────────────────────
    team_key: ClassVar[str]            # canonical team key, e.g. "developer"
    display_name: ClassVar[str]        # human-readable, e.g. "Developer"
    # Some teams (support) handle multiple sub-keys via the SAME class.
    # Subclasses should override ``aliases`` when applicable.
    aliases: ClassVar[frozenset[str]] = frozenset()

    # ── Compiled LangGraph used by run_per_employee (override) ────────────────
    graph: ClassVar[StateGraph | None] = None

    # ── Public API ────────────────────────────────────────────────────────────

    @abstractmethod
    async def run_per_employee(
        self,
        row: CanonicalRow,
        ctx: TeamContext,
    ) -> dict[str, Any]:
        """
        Score a single employee.

        Returns a dict with at least:
            {
                "employee_id": str,
                "employee_email": str,
                "final_score": float,
                "segment_a_marks": float,
                "segment_b_marks": float,
                "base_total": float,
                "reward_score": float,
                "error": str | None,
                ... team-specific extra fields ...
            }
        """
        ...

    @abstractmethod
    async def generate_report(
        self,
        run_id: int,
        emails: list[str],
        team_key: str,
        year: int,
        month: int,
        db: AsyncSession,
        **kwargs: Any,
    ) -> str | None:
        """
        Build and save the team's Excel report.

        Returns the absolute path to the saved file, or None on failure.
        """
        ...

    # ── Optional: pre-fetch bulk data once per team (e.g. support optimisation)
    async def pre_fetch_bulk(
        self,
        rows: list[CanonicalRow],
        year: int,
        month: int,
    ) -> dict[str, Any]:
        """
        Default: no bulk pre-fetch.

        Override (e.g. SupportTeam) to issue one set of team-wide MySQL
        queries and return a dict that the orchestrator stores on every
        TeamContext as ``ctx["extra"]``.
        """
        return {}
