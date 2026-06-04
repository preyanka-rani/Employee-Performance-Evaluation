"""
app/shared/__init__.py
───────────────────────
Team-agnostic infrastructure used by every team and the orchestrator.

Modules:
    excel_parser    — unified Excel parser (static + AI fallback + heuristic)
    data_sources    — read-only MySQL / GitLab / PostgreSQL clients
    persistence     — generic run-orchestration + TL-score upsert helpers
    employee_resolver — email → employee_id resolution via CRM
    state           — shared LangGraph base state types
    registry        — explicit TEAMS mapping (single source of truth)

NOTE: The registry is intentionally NOT re-exported here to avoid a circular
import — team classes are loaded lazily by ``app.shared.registry._build_teams()``.
Import registry contents directly via ``from app.shared.registry import ...``.
"""
