"""
app/services/workflows/commit_analysis.py
──────────────────────────────────────────
LangGraph workflow for analysing developer commits.

Pipeline:

    START
      ↓
    fetch_commits_node    – Fetch CommitBundles from GitLab for the period
      ↓
    analyze_bundles_node  – Send each bundle's diffs to the AI code analyser
      ↓
    aggregate_scores_node – Average all bundle scores → Component 1 score
      ↓
    store_results_node    – (pass-through; caller handles DB writes)
      ↓
    END

Why fewer nodes than MR workflow
─────────────────────────────────
CommitBasedGitLabClient already:
  • filters ignored files (_is_ignored)
  • truncates oversized diffs (_truncate_diff)
  • deduplicates files across commits (newest wins)
  • excludes merge commits
So extract/filter nodes are not needed.

The ``mr_scores`` key is kept for backward compatibility with the
DeveloperScorer, which writes these rows to the ``code_quality_scores`` table.
The ``mr_reference`` column stores the commit bundle label instead of an MR ref.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from app.core.logging_config import get_logger
from app.services.ai.code_quality import CodeQualityAnalyser, CodeQualityResult
from app.services.data_sources.commit_gitlab_client import (
    CommitBasedGitLabClient,
    CommitBundle,
)

logger = get_logger(__name__)


# ── Workflow State ────────────────────────────────────────────────────────────


class CommitAnalysisState(TypedDict):
    # Input
    employee_email: str
    gitlab_username: str
    author_email: str  # used as commit author filter (may differ from gitlab_username)
    evaluation_run_id: int
    year: int
    month: int

    # Intermediate
    bundles: list[CommitBundle]
    analysis_results: list[CodeQualityResult | None]  # one per bundle

    # Output — keys intentionally match MRAnalysisState for DeveloperScorer compat
    aggregate_score: float
    mr_scores: list[dict[str, Any]]  # rows ready for CodeQualityScore table
    error: str | None


# ── Node implementations ──────────────────────────────────────────────────────


async def fetch_commits_node(state: CommitAnalysisState) -> CommitAnalysisState:
    """
    Fetch all CommitBundles for the developer in the target month.

    Uses CommitBasedGitLabClient which combines:
      1. PostgreSQL events table → project discovery
      2. GitLab REST API         → commits + diffs per project
    """
    logger.info(
        "fetch_commits",
        username=state["gitlab_username"],
        author_email=state["author_email"],
        year=state["year"],
        month=state["month"],
    )

    client = CommitBasedGitLabClient()
    try:
        bundles = await client.get_developer_commit_bundles(
            username=state["gitlab_username"],
            author_email=state["author_email"],
            year=state["year"],
            month=state["month"],
        )
    except Exception as exc:
        logger.error("fetch_commits_failed", error=str(exc))
        return {**state, "bundles": [], "error": f"Commit fetch failed: {exc}"}
    finally:
        await client.close()

    logger.info("fetch_commits_done", bundle_count=len(bundles))
    return {**state, "bundles": bundles, "error": None}


async def analyze_bundles_node(state: CommitAnalysisState) -> CommitAnalysisState:
    """
    Analyse each CommitBundle with the AI code quality analyser.
    Runs bundles concurrently (bounded to 3 concurrent requests).
    """
    if not state["bundles"]:
        logger.info("analyze_bundles_skip", reason="no bundles")
        return {**state, "analysis_results": [], "error": None}

    analyser = CodeQualityAnalyser()
    semaphore = asyncio.Semaphore(3)

    async def _analyse_one(bundle: CommitBundle) -> CodeQualityResult | None:
        async with semaphore:
            diffs_payload = [
                {"file_path": d.file_path, "diff_content": d.diff_content}
                for d in bundle.diffs
            ]
            return await analyser.analyse_mr_diff(
                mr_reference=bundle.analysis_reference,
                diffs=diffs_payload,
            )

    tasks = [_analyse_one(b) for b in state["bundles"]]
    raw_results = await asyncio.gather(*tasks, return_exceptions=True)

    analysis_results: list[CodeQualityResult | None] = []
    for idx, result in enumerate(raw_results):
        if isinstance(result, Exception):
            logger.error(
                "bundle_analysis_exception",
                bundle=state["bundles"][idx].analysis_reference,
                error=str(result),
            )
            analysis_results.append(None)
        else:
            analysis_results.append(result)  # type: ignore[arg-type]

    return {**state, "analysis_results": analysis_results, "error": None}


async def aggregate_scores_node(state: CommitAnalysisState) -> CommitAnalysisState:
    """
    Build persistable score rows and compute the aggregate quality score.

    Falls back to 70.0 (conservative) when no bundles were successfully analysed.
    The ``mr_scores`` list mirrors the MR workflow's format so DeveloperScorer
    can write rows to ``code_quality_scores`` without modification.
    """
    mr_scores: list[dict[str, Any]] = []
    bundles = state["bundles"]

    for bundle, result in zip(bundles, state["analysis_results"]):
        if result is None:
            continue
        mr_scores.append(
            {
                "evaluation_run_id": state["evaluation_run_id"],
                "employee_email": state["employee_email"],
                # reuse mr_reference / mr_title columns for commit bundle labels
                "mr_reference": bundle.analysis_reference,
                "mr_title": bundle.analysis_title,
                "raw_score": result.score,
                "readability_score": result.readability,
                "logic_efficiency_score": result.logic_efficiency,
                "error_handling_score": result.error_handling,
                "architecture_score": result.architecture,
                "security_score": result.security,
                "reasoning": result.reasoning,
                "issues": json.dumps(result.issues),
                "model_used": result.model_used,
            }
        )

    valid_scores = [r.score for r in state["analysis_results"] if r is not None]
    if valid_scores:
        aggregate = round(sum(valid_scores) / len(valid_scores), 2)
    else:
        aggregate = 70.0

    logger.info(
        "aggregate_commit_scores",
        email=state["employee_email"],
        bundle_count=len(valid_scores),
        aggregate=aggregate,
    )
    return {**state, "mr_scores": mr_scores, "aggregate_score": aggregate}


async def store_results_node(state: CommitAnalysisState) -> CommitAnalysisState:
    """
    Pass-through node.  Actual DB writes are done by DeveloperScorer after
    the workflow completes, keeping DB logic out of LangGraph nodes.
    """
    logger.info(
        "commit_store_results_ready",
        email=state["employee_email"],
        rows=len(state["mr_scores"]),
    )
    return state


# ── Graph assembly ────────────────────────────────────────────────────────────


def build_commit_analysis_graph() -> Any:
    """Assemble and compile the LangGraph commit analysis state machine."""
    builder: StateGraph = StateGraph(CommitAnalysisState)  # type: ignore[type-arg]

    builder.add_node("fetch_commits", fetch_commits_node)
    builder.add_node("analyze_bundles", analyze_bundles_node)
    builder.add_node("aggregate_scores", aggregate_scores_node)
    builder.add_node("store_results", store_results_node)

    builder.add_edge(START, "fetch_commits")
    builder.add_edge("fetch_commits", "analyze_bundles")
    builder.add_edge("analyze_bundles", "aggregate_scores")
    builder.add_edge("aggregate_scores", "store_results")
    builder.add_edge("store_results", END)

    return builder.compile()


# Singleton compiled graph — created once at module load
commit_analysis_graph = build_commit_analysis_graph()


async def run_commit_analysis(
    employee_email: str,
    gitlab_username: str,
    author_email: str,
    evaluation_run_id: int,
    year: int,
    month: int,
) -> CommitAnalysisState:
    """
    Convenience entry-point for running the full commit analysis workflow.

    Returns the final state, which includes:
        aggregate_score : Component 1 score (0–100)
        mr_scores       : List of rows ready for DB insertion
        error           : None on success, error message on failure
    """
    initial_state: CommitAnalysisState = {
        "employee_email": employee_email,
        "gitlab_username": gitlab_username,
        "author_email": author_email,
        "evaluation_run_id": evaluation_run_id,
        "year": year,
        "month": month,
        "bundles": [],
        "analysis_results": [],
        "aggregate_score": 70.0,
        "mr_scores": [],
        "error": None,
    }

    final_state: CommitAnalysisState = await commit_analysis_graph.ainvoke(initial_state)  # type: ignore[assignment]
    return final_state
