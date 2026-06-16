"""
app/orchestrator/routing.py
───────────────────────────
Team → worker-node routing table.

The supervisor graph has a single dispatch node that fans out via
``add_conditional_edges``. The path map below is a pure dict lookup
— no if/else — so adding a new team is a one-line change: register
the team in ``app/shared/registry.py`` and add a single row to
``TEAM_TO_ROUTE_KEY``.

Every support sub-team collapses to the ``"support"`` routing key
because they share ``SupportTeam`` (sub-team identity is carried on
``ctx["team_key"]`` at runtime).
"""

from __future__ import annotations

from app.shared.state import OrchestratorState

# ── Layer 1: canonical team_key → routing key ─────────────────────────────────
# Pure dict lookup — adding a new team means adding one row here.
TEAM_TO_ROUTE_KEY: dict[str, str] = {
    "developer": "developer",
    "impl_its": "support",
    "onsite_support": "support",
    "production": "support",
    "tech_support": "support",
    "support": "support",
    "gsd": "gsd",
    "cirt_infra": "cirt_infra",
    "application": "application",
    "sqa": "sqa",
    "hajj_helpdesk": "hajj_helpdesk",
    "supply_chain": "supply_chain",
    "finance": "finance",
}

# ── Layer 2: routing key → worker node name (LangGraph path map) ──────────────
PATH_MAP: dict[str, str] = {
    "developer": "score_developer",
    "support": "score_support",
    "gsd": "score_gsd",
    "cirt_infra": "score_cirt_infra",
    "application": "score_application",
    "sqa": "score_sqa",
    "hajj_helpdesk": "score_hajj_helpdesk",
    "supply_chain": "score_supply_chain",
    "finance": "score_finance",
}


def route_to_team_worker(state: OrchestratorState) -> str:
    """
    Return the routing key (``"developer"`` or ``"support"``) for the
    current state.  LangGraph then uses ``PATH_MAP`` to convert the
    routing key into the actual worker node name.
    """
    team_key: str = state.get("team_key", "")
    return TEAM_TO_ROUTE_KEY[team_key]


__all__ = ["TEAM_TO_ROUTE_KEY", "PATH_MAP", "route_to_team_worker"]
