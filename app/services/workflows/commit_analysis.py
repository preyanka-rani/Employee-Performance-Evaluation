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
from pathlib import Path
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from app.core.logging_config import get_logger
from app.services.ai.code_quality import CodeQualityAnalyser, CodeQualityResult
from app.services.data_sources.commit_gitlab_client import (
    CommitBasedGitLabClient,
    CommitBundle,
)

logger = get_logger(__name__)

# ── Doc/config-only detection ─────────────────────────────────────────────────
# Bundles whose every file matches these extensions/basenames are documentation
# or configuration files with no executable logic.  Sending them to the AI
# wastes tokens and always returns the neutral 70 default — skip them.
_DOC_ONLY_EXTENSIONS: frozenset[str] = frozenset(
    {".md", ".txt", ".rst", ".markdown", ".mdx"}
)
_DOC_ONLY_BASENAMES: frozenset[str] = frozenset(
    {
        ".gitignore",
        ".gitattributes",
        ".editorconfig",
        ".npmrc",
        ".nvmrc",
        "license",
        "licence",
        "changelog",
        "readme",
        "notice",
        "authors",
        "contributors",
    }
)


def _is_doc_only_bundle(bundle: CommitBundle) -> bool:
    """
    Return True when every file in the bundle is a documentation or config file.
    Such bundles should not be sent to the AI — they always score ~70 and waste
    API quota.

    A bundle with NO diffs is NOT considered doc-only (it is empty/broken).
    """
    if not bundle.diffs:
        return False
    for diff in bundle.diffs:
        fp = diff.file_path.lower()
        ext = Path(fp).suffix.lower()
        basename = Path(fp).name.lower()
        if ext not in _DOC_ONLY_EXTENSIONS and basename not in _DOC_ONLY_BASENAMES:
            return False  # at least one real code file found
    return True


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
        # ── No analyzable diffs: developer worked on config/doc files only ──
        # Give a neutral score of 50 so the project is included in the report
        # and the developer receives credit for the work (e.g. README updates,
        # CI config, .env templates).  These bundles ARE included in the
        # weighted aggregate (unlike doc_config_skipped bundles which are not).
        if not bundle.diffs:
            logger.info(
                "bundle_no_code_diffs",
                bundle=bundle.analysis_reference,
            )
            return CodeQualityResult(
                score=50.0,
                readability=50.0,
                logic_efficiency=50.0,
                error_handling=50.0,
                architecture=50.0,
                security=50.0,
                reasoning=(
                    "No analyzable code diffs found — developer worked on configuration, "
                    "documentation, or other non-code files. "
                    "Neutral score of 50 assigned to acknowledge the contribution."
                ),
                issues=[],
                model_used="no_code_diffs",
            )

        # ── Skip doc/config-only bundles — no code to review ─────────────────
        if _is_doc_only_bundle(bundle):
            logger.info(
                "bundle_doc_only_skipped",
                bundle=bundle.analysis_reference,
                files=[d.file_path for d in bundle.diffs],
            )
            return CodeQualityResult(
                score=0.0,
                readability=0.0,
                logic_efficiency=0.0,
                error_handling=0.0,
                architecture=0.0,
                security=0.0,
                reasoning=(
                    "Skipped: bundle contains only documentation/configuration "
                    f"files ({len(bundle.diffs)} file(s)). No executable code to evaluate."
                ),
                issues=[],
                model_used="doc_config_skipped",
            )

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

    Aggregate formula:
        weighted_avg = Σ(commit_count_i × score_i) / Σ(commit_count_i)

    Doc/config-only bundles (model_used="doc_config_skipped") are included in
    the ``mr_scores`` DB rows for auditability but EXCLUDED from the weighted
    average — they contain no code and should not dilute the score.

    Falls back to 0.0 when no bundles were successfully analysed.
    """
    mr_scores: list[dict[str, Any]] = []
    bundles = state["bundles"]

    for bundle, result in zip(bundles, state["analysis_results"]):
        if result is None:
            # AI analysis failed — still persist bundle metadata so commit count
            # and line stats are visible in the report even when AI is unavailable.
            mr_scores.append(
                {
                    "evaluation_run_id": state["evaluation_run_id"],
                    "employee_email": state["employee_email"],
                    "mr_reference": bundle.analysis_reference,
                    "mr_title": bundle.analysis_title,
                    "raw_score": 0.0,
                    "readability_score": 0.0,
                    "logic_efficiency_score": 0.0,
                    "error_handling_score": 0.0,
                    "architecture_score": 0.0,
                    "security_score": 0.0,
                    "reasoning": (
                        "AI analysis failed — API unavailable or rate limited. "
                        "Commit and line data are preserved; re-run when the API is restored."
                    ),
                    "issues": json.dumps([]),
                    "model_used": "ai_failed",
                    "lines_added": bundle.lines_added,
                    "lines_deleted": bundle.lines_deleted,
                }
            )
            continue
        mr_scores.append(
            {
                "evaluation_run_id": state["evaluation_run_id"],
                "employee_email": state["employee_email"],
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
                # Line counts from the bundle (counted from diff content)
                "lines_added": bundle.lines_added,
                "lines_deleted": bundle.lines_deleted,
            }
        )

    # Weighted average
    # - doc_config_skipped bundles (diffs exist but all are docs): EXCLUDED
    # - no_code_diffs bundles (empty diffs, config/md-only work): INCLUDED at score 50
    # - ai_failed: EXCLUDED
    total_weight = 0
    weighted_sum = 0.0
    for bundle, result in zip(bundles, state["analysis_results"]):
        if result is None:
            continue
        if result.model_used in ("doc_config_skipped", "ai_failed"):
            continue  # no executable code — exclude from quality aggregate
        weight = max(bundle.commit_count, 1)
        weighted_sum += result.score * weight
        total_weight += weight

    aggregate = round(weighted_sum / total_weight, 2) if total_weight > 0 else 0.0

    logger.info(
        "aggregate_commit_scores",
        email=state["employee_email"],
        bundle_count=len(bundles),
        scored_bundles=total_weight,
        ai_failed=sum(1 for r in state["analysis_results"] if r is None),
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
