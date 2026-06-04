"""
app/shared/registry.py
──────────────────────
EXPLICIT team registry — single source of truth.

Design intent
─────────────
- **No magic auto-discovery.** Adding a team = one line in TEAMS below.
- **Eager validation.** Every registered class must implement TeamContract.
- **Multiple keys → same class** is supported (e.g. all 4 support sub-teams
  share one SupportTeam class; the class reads ``team_key`` from state).

How to add a new team in the future
───────────────────────────────────
1. Create ``app/teams/<new_team>/`` with ``team.py`` exporting a
   ``class <NewTeam>(TeamContract)``.
2. Add the import + one line in the TEAMS dict below, e.g.::

       from app.teams.finance.team import FinanceTeam
       TEAMS["finance"] = FinanceTeam
3. That's it. The supervisor and API pick it up automatically.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.teams.base import TeamContract


# ── The single source of truth ────────────────────────────────────────────────
# Lazy imports inside the dict to avoid circular dependencies on startup.

def _build_teams() -> dict[str, type["TeamContract"]]:
    """Build the TEAMS dict.  Called once at module import."""
    from app.teams.developer.team import DeveloperTeam
    from app.teams.support.team import SupportTeam

    return {
        # ── Developer (single team) ──────────────────────────────────────────
        "developer": DeveloperTeam,
        # ── Support sub-teams (all share the same SupportTeam class) ────────
        "impl_its": SupportTeam,
        "onsite_support": SupportTeam,
        "production": SupportTeam,
        "tech_support": SupportTeam,
        # Generic alias for any "support" mention
        "support": SupportTeam,
        # ── To add a new team in the future ────────────────────────────────
        # from app.teams.finance.team import FinanceTeam
        # "finance": FinanceTeam,
    }


TEAMS: dict[str, type["TeamContract"]] = _build_teams()


# ── Public API ────────────────────────────────────────────────────────────────


class TeamRegistry:
    """
    Read-only view over the TEAMS dict.

    Provides the supervisor with the data it needs to:
      - validate incoming ``team`` strings
      - resolve the canonical team key (with fuzzy matching)
      - look up the compiled LangGraph for the worker node
    """

    @staticmethod
    def keys() -> list[str]:
        return sorted(TEAMS.keys())

    @staticmethod
    def has(team_key: str) -> bool:
        return team_key in TEAMS

    @staticmethod
    def get(team_key: str) -> type["TeamContract"] | None:
        return TEAMS.get(team_key)

    @staticmethod
    def resolve(raw_team: str) -> str:
        """
        Normalise a free-form team name to a registered key.

        Resolution order:
          1. Lowercase + strip + direct match.
          2. Substitute ``[\\s\\- & ./,]+`` with underscore and retry.
          3. Fuzzy substring match against known keys.
          4. Raise ValueError.
        """
        import re

        if not raw_team:
            raise ValueError("team name is empty")

        lowered = raw_team.strip().lower()
        if lowered in TEAMS:
            return lowered

        normalised = re.sub(r"[\s\-&./,]+", "_", lowered)
        normalised = re.sub(r"_+", "_", normalised).strip("_")
        if normalised in TEAMS:
            return normalised

        # Fuzzy substring matching — most specific first
        fuzzy_order = [
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
            ("support", "support"),  # most generic — last
        ]
        for pattern, key in fuzzy_order:
            if pattern in normalised and key in TEAMS:
                return key

        raise ValueError(
            f"Cannot resolve team name '{raw_team}' to a known scorer. "
            f"Please use one of: {sorted(TEAMS.keys())}"
        )


def get_team(team_key: str) -> type["TeamContract"]:
    """Shorthand: return the TeamContract class for a key. Raises KeyError."""
    cls = TEAMS.get(team_key)
    if cls is None:
        raise KeyError(f"Unknown team: {team_key}")
    return cls
