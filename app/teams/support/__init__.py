"""
app/teams/support/__init__.py
────────────────────────────
Support-team worker.

A single ``SupportTeam`` class handles all four sub-teams
(impl_its, onsite_support, production, tech_support) by reading the
concrete sub-team key from ``ctx["team_key"]`` at runtime.
"""

from app.teams.support.team import SupportTeam

__all__ = ["SupportTeam"]
