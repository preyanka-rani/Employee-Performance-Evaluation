"""
app/teams/hajj_helpdesk/__init__.py
────────────────────────────────────
Hajj Helpdesk team worker.

Hajj Helpdesk shares the exact same dual-source functional logic
(CRM Logs + Support Tickets) and 80-to-100 normalization scale as
the Support team.
"""

from app.teams.hajj_helpdesk.team import HajjHelpdeskTeam

__all__ = ["HajjHelpdeskTeam"]
