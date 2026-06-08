"""
app/teams/cirt_infra/__init__.py
────────────────────────────────
CIRT & Infra team package.

Re-exports the ``CIRTInfraTeam`` class so the registry and orchestrator
can import it via ``from app.teams.cirt_infra import CIRTInfraTeam``.
"""

from app.teams.cirt_infra.team import CIRTInfraTeam

__all__ = ["CIRTInfraTeam"]
