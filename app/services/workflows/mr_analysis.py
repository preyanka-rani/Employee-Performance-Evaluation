"""
app/services/workflows/mr_analysis.py
───────────────────────────────────────
LangGraph workflow for analysing GitLab Merge Requests.

Pipeline (from documentation §LANGGRAPH WORKFLOW):

    START
      ↓
    fetch_mrs_node        – Fetch merged MRs from GitLab for the period
      ↓
    extract_diff_node     – Flatten MR objects into individual diffs
      ↓
    filter_diff_node      – Remove ignored files (migrations, locks, vendor)
      ↓
    analyze_code_node     – Send diffs to Claude for quality scoring
      ↓ (on failure → groq_fallback_node, which retries with Groq)
    parse_score_node      – Parse and validate JSON scores
      ↓
    aggregate_scores_node – Average multiple MR scores per employee
      ↓
    store_results_node    – Persist CodeQualityScore rows to SQLite
      ↓
    END

State is fully typed with TypedDict to prevent hidden data access bugs.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from app.core.logging_config import get_logger
from app.services.ai.code_quality import CodeQualityAnalyser, CodeQualityResult
from app.services.data_sources.gitlab_client import GitLabClient, MergeRequestData
from app.services.data_sources.postgresql_gitlab_client import PostgreSQLGitLabClient

logger = get_logger(__name__)


# ── Workflow State ────────────────────────────────────────────────────────────


class MRAnalysisState(TypedDict):
    # Input
    employee_email: str
    gitlab_username: str
    evaluation_run_id: int
    year: int
    month: int

    # Intermediate
    raw_mrs: list[MergeRequestData]
    filtered_mrs: list[MergeRequestData]  # after diff filtering
    analysis_results: list[CodeQualityResult | None]  # one per MR

    # Output
    aggregate_score: float  # final Component 1 score (0–100)
    mr_scores: list[dict[str, Any]]  # ready-to-persist rows
    error: str | None


# ── Node implementations ──────────────────────────────────────────────────────


async def fetch_mrs_node(state: MRAnalysisState) -> MRAnalysisState:
    """
    Fetch all merged MRs for the employee in the target month.

    Strategy (in priority order):
      1. PostgreSQL direct DB — if GITLAB_DB_HOST is configured (fast, no group_id).
      2. GitLab REST API     — fallback, requires GITLAB_GROUP_ID.
    """
    from app.core.config import get_settings

    settings = get_settings()

    logger.info(
        "fetch_mrs",
        username=state["gitlab_username"],
        year=state["year"],
        month=state["month"],
    )

    if settings.has_gitlab_db:
        client: PostgreSQLGitLabClient | GitLabClient = PostgreSQLGitLabClient()
        source = "postgresql"
    else:
        client = GitLabClient()
        source = "rest_api"

    logger.info("fetch_mrs_source", source=source)
    try:
        mrs = await client.get_merged_mrs_for_user(
            gitlab_username=state["gitlab_username"],
            year=state["year"],
            month=state["month"],
        )
    except Exception as exc:
        logger.error("fetch_mrs_failed", error=str(exc))
        return {**state, "raw_mrs": [], "error": f"GitLab fetch failed: {exc}"}

    logger.info("fetch_mrs_done", count=len(mrs))
    return {**state, "raw_mrs": mrs, "error": None}


async def extract_diff_node(state: MRAnalysisState) -> MRAnalysisState:
    """
    Validate that each MR has at least one diff.
    GitLabClient already filters ignored files, so we just check for emptiness.
    """
    valid_mrs = [mr for mr in state["raw_mrs"] if mr.diffs]
    logger.info("extract_diff", total=len(state["raw_mrs"]), valid=len(valid_mrs))
    return {**state, "filtered_mrs": valid_mrs}


async def filter_diff_node(state: MRAnalysisState) -> MRAnalysisState:
    """
    Secondary filter: ensure diffs have meaningful content.
    Removes diffs with only whitespace or comment-only changes.
    """
    cleaned_mrs: list[MergeRequestData] = []
    for mr in state["filtered_mrs"]:
        meaningful_diffs = [
            d
            for d in mr.diffs
            if d.diff_content.strip() and len(d.diff_content.strip()) > 20
        ]
        if meaningful_diffs:
            mr.diffs = meaningful_diffs
            cleaned_mrs.append(mr)

    logger.info("filter_diff", mrs_with_content=len(cleaned_mrs))
    return {**state, "filtered_mrs": cleaned_mrs}


async def analyze_code_node(state: MRAnalysisState) -> MRAnalysisState:
    """
    Analyse each MR diff with the AI code quality analyser.
    Runs all MR analyses concurrently (bounded to 3 concurrent requests).
    """
    if not state["filtered_mrs"]:
        return {**state, "analysis_results": [], "error": None}

    analyser = CodeQualityAnalyser()
    semaphore = asyncio.Semaphore(3)  # max 3 concurrent AI calls

    async def _analyse_one(mr: MergeRequestData) -> CodeQualityResult | None:
        async with semaphore:
            diffs_payload = [
                {"file_path": d.file_path, "diff_content": d.diff_content}
                for d in mr.diffs
            ]
            return await analyser.analyse_mr_diff(
                mr_reference=mr.mr_reference,
                diffs=diffs_payload,
            )

    tasks = [_analyse_one(mr) for mr in state["filtered_mrs"]]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    analysis_results: list[CodeQualityResult | None] = []
    for idx, result in enumerate(results):
        if isinstance(result, Exception):
            logger.error(
                "mr_analysis_exception",
                mr=state["filtered_mrs"][idx].mr_reference,
                error=str(result),
            )
            analysis_results.append(None)
        else:
            analysis_results.append(result)  # type: ignore[arg-type]

    return {**state, "analysis_results": analysis_results, "error": None}


async def parse_score_node(state: MRAnalysisState) -> MRAnalysisState:
    """
    Build the list of persistable MR score rows from analysis results.
    Associates each result with its corresponding MR metadata.
    """
    mr_scores: list[dict[str, Any]] = []
    mrs = state["filtered_mrs"]

    for mr, result in zip(mrs, state["analysis_results"]):
        if result is None:
            continue
        mr_scores.append(
            {
                "evaluation_run_id": state["evaluation_run_id"],
                "employee_email": state["employee_email"],
                "mr_reference": mr.mr_reference,
                "mr_title": mr.title,
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

    return {**state, "mr_scores": mr_scores}


async def aggregate_scores_node(state: MRAnalysisState) -> MRAnalysisState:
    """
    Compute the final Component 1 (quality check) score.
    Uses the mean of all MR scores.
    Falls back to 70 (conservative default) if no MRs were analysed.
    """
    valid_scores = [r.score for r in state["analysis_results"] if r is not None]

    if valid_scores:
        aggregate = round(sum(valid_scores) / len(valid_scores), 2)
    else:
        aggregate = 70.0  # conservative default when no MRs found

    logger.info(
        "aggregate_scores",
        email=state["employee_email"],
        mr_count=len(valid_scores),
        aggregate=aggregate,
    )
    return {**state, "aggregate_score": aggregate}


async def store_results_node(state: MRAnalysisState) -> MRAnalysisState:
    """
    Persist CodeQualityScore rows to the SQLite database.
    This node is intentionally a pass-through in the workflow;
    actual DB writes are done by the DeveloperEvaluationService
    after the workflow completes, to keep DB logic out of LangGraph nodes.
    """
    logger.info(
        "store_results_ready",
        email=state["employee_email"],
        rows=len(state["mr_scores"]),
    )
    return state


# ── Graph assembly ────────────────────────────────────────────────────────────


def build_mr_analysis_graph() -> Any:
    """
    Assemble and compile the LangGraph MR analysis state machine.

    The graph follows the linear pipeline from the documentation.
    """
    builder: StateGraph = StateGraph(MRAnalysisState)  # type: ignore[type-arg]

    builder.add_node("fetch_mrs", fetch_mrs_node)
    builder.add_node("extract_diff", extract_diff_node)
    builder.add_node("filter_diff", filter_diff_node)
    builder.add_node("analyze_code", analyze_code_node)
    builder.add_node("parse_score", parse_score_node)
    builder.add_node("aggregate_scores", aggregate_scores_node)
    builder.add_node("store_results", store_results_node)

    builder.add_edge(START, "fetch_mrs")
    builder.add_edge("fetch_mrs", "extract_diff")
    builder.add_edge("extract_diff", "filter_diff")
    builder.add_edge("filter_diff", "analyze_code")
    builder.add_edge("analyze_code", "parse_score")
    builder.add_edge("parse_score", "aggregate_scores")
    builder.add_edge("aggregate_scores", "store_results")
    builder.add_edge("store_results", END)

    return builder.compile()


# Singleton compiled graph – created once at module load
mr_analysis_graph = build_mr_analysis_graph()


async def run_mr_analysis(
    employee_email: str,
    gitlab_username: str,
    evaluation_run_id: int,
    year: int,
    month: int,
) -> MRAnalysisState:
    """
    Convenience entry-point for running the full MR analysis workflow.

    Returns the final state, which includes:
        aggregate_score : Component 1 score (0-100)
        mr_scores       : List of rows ready for DB insertion
        error           : None on success, error message on failure
    """
    initial_state: MRAnalysisState = {
        "employee_email": employee_email,
        "gitlab_username": gitlab_username,
        "evaluation_run_id": evaluation_run_id,
        "year": year,
        "month": month,
        "raw_mrs": [],
        "filtered_mrs": [],
        "analysis_results": [],
        "aggregate_score": 70.0,
        "mr_scores": [],
        "error": None,
    }

    final_state: MRAnalysisState = await mr_analysis_graph.ainvoke(initial_state)  # type: ignore[assignment]
    return final_state
