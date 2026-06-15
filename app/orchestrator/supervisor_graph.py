"""
app/orchestrator/supervisor_graph.py
────────────────────────────────────
Top-level supervisor LangGraph.

Topology
────────

    START
      ↓
    parse_excel
      ↓
    resolve_employee_ids
      ↓
    upsert_employees_and_tl  (also creates the EvaluationRun → state["run_id"])
      ↓
    pre_fetch_bulk
      ↓
    [add_conditional_edges]  → score_developer   (team_key == "developer")
                                → score_support    (any support sub-team)
                                → score_cirt_infra (team_key == "cirt_infra")
                                → score_sqa        (team_key == "sqa")
                                        ↓               ↓               ↓               ↓
    [converge]                    generate_report
      ↓
    finalise_run
      ↓
    build_response
      ↓
    END

The conditional edge is a pure dict lookup (no if/else) — see
``app/orchestrator/routing.py``.
"""

from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging_config import get_logger
from app.orchestrator.nodes import (
    build_response_node,
    finalise_run_node,
    generate_report_node,
    parse_excel_node,
    pre_fetch_bulk_node,
    resolve_employee_ids_node,
    score_application_node,
    score_cirt_infra_node,
    score_developer_node,
    score_gsd_node,
    score_hajj_helpdesk_node,
    score_sqa_node,
    score_supply_chain_node,
    score_support_node,
    upsert_employees_and_tl_node,
)
from app.orchestrator.routing import PATH_MAP, route_to_team_worker
from app.shared.state import OrchestratorState

logger = get_logger(__name__)


# ── Graph construction ────────────────────────────────────────────────────────


def build_supervisor_graph() -> Any:
    """
    Build and compile the supervisor StateGraph.

    The graph is a singleton per import — call this once at startup
    and reuse the result for every request.
    """
    builder = StateGraph(OrchestratorState)

    # ── Sequential nodes ─────────────────────────────────────────────────
    builder.add_node("parse_excel", parse_excel_node)
    builder.add_node("resolve_employee_ids", resolve_employee_ids_node)
    builder.add_node("upsert_employees_and_tl", upsert_employees_and_tl_node)
    builder.add_node("pre_fetch_bulk", pre_fetch_bulk_node)

    # ── Worker fan-out (routed via add_conditional_edges) ────────────────
    builder.add_node("score_developer", score_developer_node)
    builder.add_node("score_support", score_support_node)
    builder.add_node("score_gsd", score_gsd_node)
    builder.add_node("score_cirt_infra", score_cirt_infra_node)
    builder.add_node("score_application", score_application_node)
    builder.add_node("score_sqa", score_sqa_node)
    builder.add_node("score_hajj_helpdesk", score_hajj_helpdesk_node)
    builder.add_node("score_supply_chain", score_supply_chain_node)

    # ── Convergent back-half ─────────────────────────────────────────────
    builder.add_node("generate_report", generate_report_node)
    builder.add_node("finalise_run", finalise_run_node)
    builder.add_node("build_response", build_response_node)

    # ── Edges ────────────────────────────────────────────────────────────
    builder.add_edge(START, "parse_excel")
    builder.add_edge("parse_excel", "resolve_employee_ids")
    builder.add_edge("resolve_employee_ids", "upsert_employees_and_tl")
    builder.add_edge("upsert_employees_and_tl", "pre_fetch_bulk")

    # Conditional fan-out — path map converts routing key → node name
    builder.add_conditional_edges(
        "pre_fetch_bulk",
        route_to_team_worker,
        PATH_MAP,
    )

    # All workers converge on generate_report
    builder.add_edge("score_developer", "generate_report")
    builder.add_edge("score_support", "generate_report")
    builder.add_edge("score_gsd", "generate_report")
    builder.add_edge("score_cirt_infra", "generate_report")
    builder.add_edge("score_application", "generate_report")
    builder.add_edge("score_sqa", "generate_report")
    builder.add_edge("score_hajj_helpdesk", "generate_report")
    builder.add_edge("score_supply_chain", "generate_report")

    builder.add_edge("generate_report", "finalise_run")
    builder.add_edge("finalise_run", "build_response")
    builder.add_edge("build_response", END)

    return builder.compile()


# Module-level singleton
_supervisor_graph = build_supervisor_graph()


# ── Public entry point ────────────────────────────────────────────────────────


async def run_supervisor(
    *,
    file_bytes: bytes,
    raw_team_input: str,
    year: int,
    month: int,
    db: AsyncSession,
) -> dict[str, Any]:
    """
    One-call helper invoked by the API endpoint.

    Returns the ``summary`` dict from ``build_response_node``.
    """
    log = logger.bind(team=raw_team_input, year=year, month=month)
    log.info("supervisor_start")

    initial_state: OrchestratorState = {
        "file_bytes": file_bytes,
        "raw_team_input": raw_team_input,
        "year": year,
        "month": month,
        "db": db,
    }
    final_state: OrchestratorState = await _supervisor_graph.ainvoke(initial_state)  # type: ignore[assignment]

    summary = final_state.get("summary", {})
    log.info("supervisor_done", **{k: v for k, v in summary.items() if k != "errors"})
    return summary
