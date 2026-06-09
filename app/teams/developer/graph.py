"""
app/teams/developer/graph.py
─────────────────────────────
Developer worker LangGraph — wraps the per-employee scoring flow that was
previously in app/services/scoring/developer.py:DeveloperScorer.calculate().

Pipeline
────────

    START
      ↓
    fetch_gitlab_quality  — run_commit_analysis() + issue_stats + line_stats
      ↓
    fetch_crm             — CRM work logs + descriptions (asyncio.gather)
      ↓
    fetch_hr              — MySQL HR attendance
      ↓
    load_tl               — load TLAssessmentScore from SQLite
      ↓
    compute_component1    — code-quality, resolution, reopens, line tier scores
      ↓
    compute_segment_a     — work_log_score, sentiment_score, component2
      ↓
    compute_segment_b     — attendance_marks, TL total, segment_b_marks
      ↓
    compute_final         — base_total, reward, final_score
      ↓
    END

The graph is **pure** — no DB writes happen inside nodes. All persistence
is performed by ``DeveloperTeam.run_per_employee()`` after the graph
returns, in the same order as the legacy DeveloperScorer.calculate().

This guarantees:
  • 100% numerical parity (formulas + tier boundaries + weights unchanged)
  • Identical row-write order to the legacy implementation
  • Easy unit-testing of the graph in isolation
"""

from __future__ import annotations

import asyncio
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging_config import get_logger
from app.repositories.score_repository import TLAssessmentRepository
from app.services.ai.sentiment import compute_employee_sentiment_score
from app.shared.data_sources.commit_gitlab_client import CommitBasedGitLabClient
from app.shared.data_sources.mysql_client import MySQLCRMClient, MySQLHRClient
from app.shared.data_sources.postgresql_gitlab_client import PostgreSQLGitLabClient
from app.teams.developer.commit_analysis import run_commit_analysis
from app.teams.developer.formulas import (
    compute_component1,
    compute_final_score,
    compute_reward,
    compute_segment_a,
    compute_segment_b,
    normalise_lines_added,
    normalise_lines_deleted,
    normalise_work_hours,
)

logger = get_logger(__name__)


# ── Workflow state ────────────────────────────────────────────────────────────


class DeveloperWorkerState(TypedDict, total=False):
    # ── Inputs (set by run_per_employee before invoking) ──────────────────────
    employee_id: str
    employee_email: str
    employee_name: str
    gitlab_username: str
    year: int
    month: int
    run_id: int
    db: AsyncSession  # passed for TL lookup only (graph doesn't write)

    # ── After fetch_gitlab_quality_node ───────────────────────────────────────
    code_quality_ai: float
    mr_scores: list[dict]
    issue_stats: dict[str, int]
    line_stats: dict[str, int]

    # ── After fetch_crm_node ─────────────────────────────────────────────────
    crm_log_records: list[dict]
    crm_description_records: list[dict]

    # ── After fetch_hr_node ──────────────────────────────────────────────────
    attendance_records: list[dict]

    # ── After load_tl_node ───────────────────────────────────────────────────
    tl_problem_solving: float
    tl_kpi: float
    tl_general: float

    # ── After compute_component1_node ─────────────────────────────────────────
    resolution_rate: float
    reopen_quality: float
    lines_added_score: float
    lines_deleted_score: float
    quality_check: float

    # ── After compute_segment_a_node ──────────────────────────────────────────
    total_hours: float
    work_log_score: float
    sentiment_avg: float
    avg_polarity: float
    component2: float
    segment_a_score: float
    segment_a_marks: float

    # ── After compute_segment_b_node ──────────────────────────────────────────
    attendance_score: float
    attendance_present: int
    attendance_late: int
    attendance_work_days: int
    attendance_marks: float
    tl_total: float
    segment_b_marks: float

    # ── After compute_final_node ─────────────────────────────────────────────
    base_total: float
    reward: float
    final_score: float

    # ── Error tracking ───────────────────────────────────────────────────────
    fetch_error: str | None


# ── Node implementations ──────────────────────────────────────────────────────


async def fetch_gitlab_quality_node(state: DeveloperWorkerState) -> dict:
    """
    Run the existing commit-analysis LangGraph and fetch issue/line stats.

    Mirrors the legacy DeveloperScorer.calculate() steps 1 + 1.5.
    """
    log = logger.bind(
        employee_email=state["employee_email"],
        year=state["year"],
        month=state["month"],
    )
    log.info("developer_fetch_gitlab_start")

    # 1. Code quality via existing LangGraph
    try:
        commit_state = await run_commit_analysis(
            employee_email=state["employee_email"],
            gitlab_username=state["gitlab_username"] or state["employee_id"],
            author_email=state["employee_email"],
            evaluation_run_id=state["run_id"],
            year=state["year"],
            month=state["month"],
        )
        code_quality_ai: float = commit_state["aggregate_score"]
        mr_scores: list[dict] = commit_state["mr_scores"]
    except Exception as exc:
        log.error("commit_analysis_failed", error=str(exc))
        return {
            "code_quality_ai": 0.0,
            "mr_scores": [],
            "issue_stats": {"total_assigned": 0, "total_resolved": 0, "total_reopens": 0},
            "line_stats": {"total_additions": 0, "total_deletions": 0},
            "fetch_error": f"Commit analysis failed: {exc}",
        }

    # 1.5  Fetch issue stats + line stats for Component 1
    gitlab_username = state["gitlab_username"] or state["employee_id"]
    pg_client = PostgreSQLGitLabClient()
    commit_client = CommitBasedGitLabClient()
    try:
        issue_stats = await pg_client.get_issue_stats(
            user_email=state["employee_email"],
            year=state["year"],
            month=state["month"],
        )
        line_stats = await commit_client.get_developer_line_stats(
            username=gitlab_username,
            author_email=state["employee_email"],
            year=state["year"],
            month=state["month"],
        )
    except Exception as exc:
        log.error("gitlab_issue_or_line_stats_failed", error=str(exc))
        issue_stats = {"total_assigned": 0, "total_resolved": 0, "total_reopens": 0}
        line_stats = {"total_additions": 0, "total_deletions": 0}
    finally:
        await pg_client.close()
        await commit_client.close()

    log.info(
        "developer_fetch_gitlab_done",
        code_quality=code_quality_ai,
        bundles=len(mr_scores),
    )
    return {
        "code_quality_ai": code_quality_ai,
        "mr_scores": mr_scores,
        "issue_stats": issue_stats,
        "line_stats": line_stats,
    }


async def fetch_crm_node(state: DeveloperWorkerState) -> dict:
    """
    Fetch work log hours and descriptions from MySQL CRM.
    Mirrors legacy steps 2 + the description fetch used for sentiment.
    """
    log = logger.bind(employee_email=state["employee_email"])
    log.info("developer_fetch_crm_start")

    crm = MySQLCRMClient()
    try:
        log_records = await crm.get_developer_work_logs(
            employee_ids=[state["employee_id"]],
            year=state["year"],
            month=state["month"],
        )
        descriptions = await crm.get_developer_log_descriptions(
            employee_ids=[state["employee_id"]],
            year=state["year"],
            month=state["month"],
        )
    except Exception as exc:
        log.error("crm_fetch_failed", error=str(exc))
        return {
            "crm_log_records": [],
            "crm_description_records": [],
        }
    finally:
        await crm.close()

    log.info(
        "developer_fetch_crm_done",
        log_records=len(log_records),
        descriptions=len(descriptions),
    )
    return {
        "crm_log_records": log_records,
        "crm_description_records": descriptions,
    }


async def fetch_hr_node(state: DeveloperWorkerState) -> dict:
    """Fetch attendance from MySQL HR. Mirrors legacy step 3."""
    log = logger.bind(employee_email=state["employee_email"])
    log.info("developer_fetch_hr_start")

    hr = MySQLHRClient()
    try:
        attendance_records = await hr.get_attendance(
            employee_ids=[state["employee_id"]],
            year=state["year"],
            month=state["month"],
        )
    except Exception as exc:
        log.error("hr_fetch_failed", error=str(exc))
        return {"attendance_records": []}
    finally:
        await hr.close()

    log.info(
        "developer_fetch_hr_done",
        attendance_records=len(attendance_records),
    )
    return {"attendance_records": attendance_records}


async def load_tl_node(state: DeveloperWorkerState) -> dict:
    """
    Load TL assessment from SQLite. Falls back to mid-range defaults
    (5.0 / 7.5 / 7.5) if no row exists for this period.
    Mirrors legacy step 4.
    """
    log = logger.bind(employee_email=state["employee_email"])
    db: AsyncSession = state["db"]

    tl_repo = TLAssessmentRepository(db)
    tl_score = await tl_repo.get_for_employee_period(
        employee_email=state["employee_email"],
        year=state["year"],
        month=state["month"],
        evaluation_run_id=state["run_id"],
    )

    if tl_score:
        problem_solving = float(tl_score.problem_solving)
        kpi = float(tl_score.kpi)
        general = float(tl_score.general)
    else:
        log.warning("tl_score_not_found_using_defaults")
        problem_solving = 5.0
        kpi = 7.5
        general = 7.5

    log.info(
        "tl_loaded",
        problem_solving=problem_solving,
        kpi=kpi,
        general=general,
    )
    return {
        "tl_problem_solving": problem_solving,
        "tl_kpi": kpi,
        "tl_general": general,
    }


async def compute_component1_node(state: DeveloperWorkerState) -> dict:
    """Compute Component 1 (code quality weighted composite)."""
    log = logger.bind(employee_email=state["employee_email"])

    issue_stats = state["issue_stats"]
    line_stats = state["line_stats"]
    code_quality_ai = state["code_quality_ai"]

    total_assigned: int = issue_stats["total_assigned"]
    total_resolved: int = issue_stats["total_resolved"]
    total_reopens: int = issue_stats["total_reopens"]

    resolution_rate: float = (
        (total_resolved / total_assigned * 100) if total_assigned > 0 else 0.0
    )
    reopen_rate: float = (
        (total_reopens / total_assigned * 100) if total_assigned > 0 else 0.0
    )
    reopen_quality: float = max(0.0, min(100.0, 100.0 - reopen_rate))

    lines_added_score: float = normalise_lines_added(line_stats["total_additions"])
    lines_deleted_score: float = normalise_lines_deleted(line_stats["total_deletions"])

    quality_check: float = compute_component1(
        code_quality=code_quality_ai,
        resolution_rate=resolution_rate,
        reopen_quality=reopen_quality,
        lines_added_score=lines_added_score,
        lines_deleted_score=lines_deleted_score,
    )

    log.info(
        "component1_computed",
        code_quality=code_quality_ai,
        resolution_rate=round(resolution_rate, 2),
        reopen_quality=round(reopen_quality, 2),
        lines_added_score=lines_added_score,
        lines_deleted_score=lines_deleted_score,
        component1=quality_check,
    )
    return {
        "resolution_rate": resolution_rate,
        "reopen_quality": reopen_quality,
        "lines_added_score": lines_added_score,
        "lines_deleted_score": lines_deleted_score,
        "quality_check": quality_check,
    }


async def compute_segment_a_node(state: DeveloperWorkerState) -> dict:
    """
    Compute Segment A from work logs + sentiment.
    Mirrors legacy step 2 (work_log_score) + step 3 (sentiment) + step 6 (segment_a).
    """
    log = logger.bind(employee_email=state["employee_email"])

    # Sum hours (records are already filtered by employee_id in fetch_crm_node)
    total_hours = sum(
        float(row.get("total_hours", 0))
        for row in state["crm_log_records"]
    )
    work_log_score = normalise_work_hours(total_hours)

    # Sentiment from descriptions (already filtered by employee_id)
    desc_texts = [
        str(row.get("description", ""))
        for row in state["crm_description_records"]
    ]
    sentiment_avg, avg_polarity = compute_employee_sentiment_score(desc_texts)

    _, segment_a_marks = compute_segment_a(
        quality_check=state["quality_check"],
        work_log_score=work_log_score,
        sentiment_score=sentiment_avg,
    )
    component2 = round(work_log_score * 0.9 + sentiment_avg * 0.1, 4)
    segment_a_score = round((state["quality_check"] + component2) / 2, 4)

    log.info(
        "segment_a_computed",
        total_hours=total_hours,
        work_log_score=work_log_score,
        sentiment_score=sentiment_avg,
        component2=component2,
        segment_a_marks=segment_a_marks,
    )
    return {
        "total_hours": total_hours,
        "work_log_score": work_log_score,
        "sentiment_avg": sentiment_avg,
        "avg_polarity": avg_polarity,
        "component2": component2,
        "segment_a_score": segment_a_score,
        "segment_a_marks": segment_a_marks,
    }


async def compute_segment_b_node(state: DeveloperWorkerState) -> dict:
    """
    Compute Segment B from attendance + TL scores.
    Mirrors legacy step 4 (attendance) + step 5 (TL) + step 6 (segment_b).
    """
    log = logger.bind(employee_email=state["employee_email"])

    att_row = next(iter(state["attendance_records"]), None)

    if att_row:
        attendance_score = float(att_row.get("attendance_score", 60.0))
        present = int(att_row.get("present", 0))
        late = int(att_row.get("late", 0))
        actual_work_days = int(att_row.get("actual_work_days", 22))
    else:
        attendance_score = 60.0
        present = 0
        late = 0
        actual_work_days = 22

    attendance_marks = attendance_score / 10

    tl_total = (
        state["tl_problem_solving"] + state["tl_kpi"] + state["tl_general"]
    )
    segment_b_marks = compute_segment_b(
        attendance_score=attendance_score,
        problem_solving=state["tl_problem_solving"],
        kpi=state["tl_kpi"],
        general=state["tl_general"],
    )

    log.info(
        "segment_b_computed",
        attendance_score=attendance_score,
        attendance_marks=attendance_marks,
        tl_total=tl_total,
        segment_b_marks=segment_b_marks,
    )
    return {
        "attendance_score": attendance_score,
        "attendance_present": present,
        "attendance_late": late,
        "attendance_work_days": actual_work_days,
        "attendance_marks": attendance_marks,
        "tl_total": tl_total,
        "segment_b_marks": segment_b_marks,
    }


async def compute_final_node(state: DeveloperWorkerState) -> dict:
    """Compute base_total, reward, final_score."""
    log = logger.bind(employee_email=state["employee_email"])

    base_total = round(state["segment_a_marks"] + state["segment_b_marks"], 4)
    reward = compute_reward(
        attendance_score=state["attendance_score"],
        log_hour_score=state["work_log_score"],
        tl_total=state["tl_total"],
        quality_check=state["quality_check"],
    )
    final = compute_final_score(base_total, reward)

    log.info(
        "final_computed",
        base_total=base_total,
        reward=reward,
        final_score=final,
    )
    return {
        "base_total": base_total,
        "reward": reward,
        "final_score": final,
    }


# ── Graph construction ────────────────────────────────────────────────────────


def build_developer_graph() -> Any:
    """Build and compile the developer worker StateGraph."""
    builder = StateGraph(DeveloperWorkerState)

    builder.add_node("fetch_gitlab_quality", fetch_gitlab_quality_node)
    builder.add_node("fetch_crm", fetch_crm_node)
    builder.add_node("fetch_hr", fetch_hr_node)
    builder.add_node("load_tl", load_tl_node)
    builder.add_node("compute_component1", compute_component1_node)
    builder.add_node("compute_segment_a", compute_segment_a_node)
    builder.add_node("compute_segment_b", compute_segment_b_node)
    builder.add_node("compute_final", compute_final_node)

    builder.add_edge(START, "fetch_gitlab_quality")
    builder.add_edge("fetch_gitlab_quality", "fetch_crm")
    builder.add_edge("fetch_crm", "fetch_hr")
    builder.add_edge("fetch_hr", "load_tl")
    builder.add_edge("load_tl", "compute_component1")
    builder.add_edge("compute_component1", "compute_segment_a")
    builder.add_edge("compute_segment_a", "compute_segment_b")
    builder.add_edge("compute_segment_b", "compute_final")
    builder.add_edge("compute_final", END)

    return builder.compile()


# Singleton compiled graph — instantiated once at module load.
_developer_graph = build_developer_graph()


async def run_developer_worker(
    *,
    employee_id: str,
    employee_email: str,
    employee_name: str,
    gitlab_username: str,
    evaluation_run_id: int,
    year: int,
    month: int,
    db: AsyncSession,
) -> DeveloperWorkerState:
    """
    Run the full developer worker graph for one employee.

    Returns the final DeveloperWorkerState with all computed scores.
    Persistence (DB row writes) is the caller's responsibility (see
    DeveloperTeam.run_per_employee).
    """
    initial_state: DeveloperWorkerState = {
        "employee_id": employee_id,
        "employee_email": employee_email,
        "employee_name": employee_name,
        "gitlab_username": gitlab_username,
        "year": year,
        "month": month,
        "run_id": evaluation_run_id,
        "db": db,
        # Output defaults (so partial-state updates don't lose them)
        "code_quality_ai": 0.0,
        "mr_scores": [],
        "issue_stats": {"total_assigned": 0, "total_resolved": 0, "total_reopens": 0},
        "line_stats": {"total_additions": 0, "total_deletions": 0},
        "crm_log_records": [],
        "crm_description_records": [],
        "attendance_records": [],
        "tl_problem_solving": 5.0,
        "tl_kpi": 7.5,
        "tl_general": 7.5,
        "fetch_error": None,
    }
    final_state: DeveloperWorkerState = await _developer_graph.ainvoke(initial_state)  # type: ignore[assignment]
    return final_state
