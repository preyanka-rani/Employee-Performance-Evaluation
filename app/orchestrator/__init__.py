"""
app/orchestrator/__init__.py
────────────────────────────
Supervisor LangGraph that orchestrates every team worker.

Public entry point:
    build_supervisor_graph()  → compiled StateGraph
    run_supervisor(...)       → one-call helper for the API endpoint
"""

from app.orchestrator.supervisor_graph import (
    build_supervisor_graph,
    run_supervisor,
)

__all__ = ["build_supervisor_graph", "run_supervisor"]
