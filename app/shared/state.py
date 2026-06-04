"""
app/shared/state.py
───────────────────
Shared LangGraph state types used by both the orchestrator and the
team worker graphs.

The state is a TypedDict so LangGraph can merge updates from multiple nodes.
Every team worker may add its own typed fields, but the orchestrator-level
state only carries the fields the supervisor needs.
"""

from __future__ import annotations

from typing import Any, TypedDict

from sqlalchemy.ext.asyncio import AsyncSession

from app.shared.excel_parser.row_schema import CanonicalRow


class RunContext(TypedDict):
    """The immutable run-level context every worker receives."""

    run_id: int
    team_key: str
    year: int
    month: int
    db: AsyncSession
    team_display_name: str


class OrchestratorState(TypedDict, total=False):
    """
    Top-level state flowing through the supervisor graph.

    Each node returns a partial dict; LangGraph merges it into the running
    state. Fields with defaults may be filled in by later nodes.
    """

    # ── Inputs (set by API endpoint before invocation) ────────────────────────
    raw_team_input: str          # human-readable team name from the request
    year: int
    month: int
    file_bytes: bytes

    # ── After parse_excel_node ────────────────────────────────────────────────
    team_key: str                # normalised team key (e.g. "developer")
    parsed_rows: list[CanonicalRow]
    parse_warnings: list[str]
    col_names: dict[str, str]
    team_display_name: str

    # ── After resolve_employee_ids_node ───────────────────────────────────────
    rows_with_ids: list[CanonicalRow]   # patched with employee_id where resolved

    # ── After create_run_node ─────────────────────────────────────────────────
    run_id: int
    db: AsyncSession  # carried for downstream nodes (not serialised in checkpoints)

    # ── After team worker graph (filled by worker) ────────────────────────────
    processed_count: int
    failed_count: int
    successful_emails: list[str]
    scoring_errors: list[dict[str, Any]]
    team_results: list[dict[str, Any]]   # per-employee result dicts

    # ── After generate_report_node ─────────────────────────────────────────────
    report_path: str | None
    secondary_report_path: str | None    # e.g. CodeQuality for developer

    # ── Final response (set by build_response_node) ───────────────────────────
    summary: dict[str, Any]
