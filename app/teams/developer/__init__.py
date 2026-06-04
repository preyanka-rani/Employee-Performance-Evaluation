"""
app/teams/developer/__init__.py
───────────────────────────────
Developer team — uses GitLab (commits/MRs), MySQL CRM, MySQL HR.

Implementation filled in Step 2 of the refactor.
"""

from app.teams.developer.team import DeveloperTeam

__all__ = ["DeveloperTeam"]
