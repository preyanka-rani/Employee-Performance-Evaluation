"""
app/teams/gsd/__init__.py
─────────────────────────
GSD (Global Service Desk) team package.

Re-exports the ``GSDTeam`` class so the registry and orchestrator
can import it via ``from app.teams.gsd import GSDTeam``.
"""

from app.teams.gsd.team import GSDTeam

__all__ = ["GSDTeam"]
