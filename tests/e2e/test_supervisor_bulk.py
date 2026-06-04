"""
tests/e2e/test_supervisor_bulk.py
─────────────────────────────────
End-to-end tests for the supervisor LangGraph against the actual demo
Excel files in ``demo_inputs/``.

The wiring under test
─────────────────────
    POST /api/v1/evaluations/bulk-run
        → run_supervisor()  (orchestrator/__init__)
            → supervisor_graph
                → parse_excel → resolve_employee_ids → upsert → pre_fetch_bulk
                → [conditional edge] → score_developer | score_support
                → generate_report → finalise_run → build_response

External services that DO NOT need to be running
────────────────────────────────────────────────
The following are stubbed at the data-source boundary so the test runs
on a developer laptop without MySQL / GitLab / Claude credentials:

    app.shared.data_sources.mysql_client.MySQLCRMClient
    app.shared.data_sources.mysql_client.MySQLHRClient
    app.shared.data_sources.postgresql_gitlab_client.PostgreSQLGitLabClient
    app.shared.data_sources.commit_gitlab_client.CommitBasedGitLabClient
    app.teams.developer.commit_analysis.run_commit_analysis
    (Support CRM, Tickets, HR clients are stubbed in the team module)

The actual SQLite database is created fresh in-memory so the test is
truly self-contained.

Test cases
──────────
1. test_developer_bulk_run_end_to_end
       demo_inputs/developer.xlsx
       team="developer"  year=2026  month=2
       Expects:  outputs/developer/CodeQuality_Report_developer_2026_02.xlsx

2. test_support_impl_its_bulk_run_end_to_end
       demo_inputs/team lead mark april(Implementation & I.T.S).xlsx
       team="Implementation & I.T.S"  year=2026  month=4
       Expects:  outputs/support/Support_Final_Report_impl_its_2026_04.xlsx
"""

from __future__ import annotations

import os
import pathlib
import shutil
from typing import Any, AsyncGenerator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.database import Base
from app.orchestrator import run_supervisor

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
DEMO_DIR = REPO_ROOT / "demo_inputs"
DEV_EXCEL = DEMO_DIR / "developer.xlsx"
SUPPORT_EXCEL = DEMO_DIR / "team lead mark april(Implementation & I.T.S).xlsx"


# ═════════════════════════════════════════════════════════════════════════════
# Test scaffolding
# ═════════════════════════════════════════════════════════════════════════════


@pytest.fixture(scope="module")
def event_loop():
    import asyncio
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture
async def in_memory_db() -> AsyncGenerator[AsyncSession, None]:
    """Fresh in-memory SQLite session per test, with all tables created."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    SessionFactory = async_sessionmaker(engine, expire_on_commit=False)
    async with SessionFactory() as session:
        yield session

    await engine.dispose()


# ═════════════════════════════════════════════════════════════════════════════
# External-service stubs
# ═════════════════════════════════════════════════════════════════════════════


def _stub_mysql_crm() -> MagicMock:
    """
    Stub MySQLCRMClient.

    For ID resolution we return ``{email: email}`` so that any Excel row
    missing ``employee_id`` gets a deterministic fallback ID and proceeds
    through the rest of the pipeline.  This matches the support Excel
    which typically has empty ``employee_id`` cells.
    """
    instance = MagicMock()

    async def _resolve_ids(emails: list[str]) -> dict[str, str]:
        return {e.lower(): e.lower() for e in emails}

    instance.get_employee_ids_by_emails = AsyncMock(side_effect=_resolve_ids)
    instance.get_developer_work_logs = AsyncMock(return_value=[])
    instance.get_developer_log_descriptions = AsyncMock(return_value=[])
    instance.close = AsyncMock(return_value=None)
    return instance


def _stub_mysql_hr() -> MagicMock:
    """Stub MySQLHRClient: empty attendance for everyone."""
    instance = MagicMock()
    instance.get_attendance = AsyncMock(return_value=[])
    instance.close = AsyncMock(return_value=None)
    return instance


def _stub_pg_gitlab() -> MagicMock:
    """Stub PostgreSQLGitLabClient: zero issues / zero reopens."""
    instance = MagicMock()
    instance.get_issue_stats = AsyncMock(
        return_value={"total_assigned": 0, "total_resolved": 0, "total_reopens": 0}
    )
    instance.close = AsyncMock(return_value=None)
    return instance


def _stub_commit_gitlab() -> MagicMock:
    """Stub CommitBasedGitLabClient: zero lines added/deleted."""
    instance = MagicMock()
    instance.get_developer_line_stats = AsyncMock(
        return_value={"total_additions": 0, "total_deletions": 0}
    )
    instance.close = AsyncMock(return_value=None)
    return instance


def _stub_run_commit_analysis() -> AsyncMock:
    """
    Stub the entire commit-analysis LangGraph.

    Returns a deterministic 75.0 code-quality score with one bundle, so
    the developer scoring flow runs end-to-end and produces a real
    CodeQuality row.
    """
    return_value = {
        "aggregate_score": 75.0,
        "mr_scores": [
            {
                "evaluation_run_id": 0,  # patched in node before insert
                "employee_email": "",
                "mr_reference": "demo/project (2026-02, 1 commits)",
                "mr_title": "Demo commit for E2E test",
                "raw_score": 75.0,
                "readability_score": 80.0,
                "logic_efficiency_score": 70.0,
                "error_handling_score": 75.0,
                "architecture_score": 75.0,
                "security_score": 75.0,
                "reasoning": "Stubbed bundle for E2E test",
                "issues": "[]",
                "model_used": "stub",
                "lines_added": 100,
                "lines_deleted": 20,
            }
        ],
    }

    async def _fake(*args: Any, **kwargs: Any) -> dict[str, Any]:
        # Patch the evaluation_run_id and email from the kwargs the
        # developer graph passes in.
        out = {**return_value}
        out["evaluation_run_id"] = kwargs.get("evaluation_run_id", 0)
        out["employee_email"] = kwargs.get("employee_email", "")
        # Replicate the list-dict shape: only one bundle, but each item
        # is a fresh dict so callers can mutate.
        out["mr_scores"] = [{**return_value["mr_scores"][0]}]
        out["mr_scores"][0]["evaluation_run_id"] = out["evaluation_run_id"]
        out["mr_scores"][0]["employee_email"] = out["employee_email"]
        return out

    return AsyncMock(side_effect=_fake)


def _stub_support_crm() -> MagicMock:
    instance = MagicMock()
    instance.get_crm_log_hours = AsyncMock(return_value=[])
    instance.get_crm_descriptions = AsyncMock(return_value=[])
    instance.close = AsyncMock(return_value=None)
    return instance


def _stub_support_tickets() -> MagicMock:
    instance = MagicMock()
    instance.get_ticket_scores = AsyncMock(return_value=[])
    instance.close = AsyncMock(return_value=None)
    return instance


# Convenience patch context — all data-source clients in one go.
def _all_data_source_patches() -> list[Any]:
    """
    Patch the external services in the *consumer* module namespaces.

    Why consumer modules? When a module does ``from x import Y``, ``Y`` is
    bound in the consumer's namespace at import time. Patching
    ``x.Y`` only mutates the source module — the consumer still holds
    the original reference. We therefore patch the names in
    ``app.teams.developer.graph`` and ``app.teams.support.team`` directly.
    """
    return [
        # Developer graph — external data sources
        patch(
            "app.teams.developer.graph.MySQLCRMClient",
            return_value=_stub_mysql_crm(),
        ),
        patch(
            "app.teams.developer.graph.MySQLHRClient",
            return_value=_stub_mysql_hr(),
        ),
        patch(
            "app.teams.developer.graph.PostgreSQLGitLabClient",
            return_value=_stub_pg_gitlab(),
        ),
        patch(
            "app.teams.developer.graph.CommitBasedGitLabClient",
            return_value=_stub_commit_gitlab(),
        ),
        # The commit-analysis LangGraph itself
        patch(
            "app.teams.developer.graph.run_commit_analysis",
            side_effect=_stub_run_commit_analysis(),
        ),
        # Support team — external data sources
        patch(
            "app.teams.support.team.SupportCRMClient",
            return_value=_stub_support_crm(),
        ),
        patch(
            "app.teams.support.team.SupportTicketsClient",
            return_value=_stub_support_tickets(),
        ),
        patch(
            "app.teams.support.team.MySQLHRClient",
            return_value=_stub_mysql_hr(),
        ),
        # The support workflow's own imports — must be patched because
        # ``run_support_evaluation`` uses its own bound references.
        patch(
            "app.teams.support.graph.SupportCRMClient",
            return_value=_stub_support_crm(),
        ),
        patch(
            "app.teams.support.graph.SupportTicketsClient",
            return_value=_stub_support_tickets(),
        ),
        patch(
            "app.teams.support.graph.MySQLHRClient",
            return_value=_stub_mysql_hr(),
        ),
        # Orchestrator — MySQL CRM client used by resolve_employee_ids_node
        patch(
            "app.orchestrator.nodes.MySQLCRMClient",
            return_value=_stub_mysql_crm(),
        ),
    ]


# ═════════════════════════════════════════════════════════════════════════════
# Test 1 — developer bulk run
# ═════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_developer_bulk_run_end_to_end(
    in_memory_db: AsyncSession, tmp_path: pathlib.Path
) -> None:
    """
    Run the supervisor on the developer demo Excel and verify the
    CodeQuality report lands at the canonical path.
    """
    assert DEV_EXCEL.exists(), f"Missing demo file: {DEV_EXCEL}"
    file_bytes = DEV_EXCEL.read_bytes()

    # Run the supervisor with all external sources stubbed.
    # Switch into the temp output dir so reports go there.
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        with _multi_context(_all_data_source_patches()):
            summary = await run_supervisor(
                file_bytes=file_bytes,
                raw_team_input="developer",
                year=2026,
                month=2,
                db=in_memory_db,
            )
    finally:
        os.chdir(cwd)

    # ── Assertions on the supervisor summary ────────────────────────────
    assert summary["team"] == "developer"
    assert summary["year"] == 2026
    assert summary["month"] == 2
    assert summary["status"] in ("success", "partial")
    assert summary["processed_count"] >= 1
    assert summary["failed_count"] == 0

    # ── Report file: the canonical developer CodeQuality report ─────────
    expected = tmp_path / "outputs" / "developer" / "CodeQuality_Report_developer_2026_02.xlsx"
    assert expected.exists(), f"Missing report: {expected}"
    assert expected.stat().st_size > 1000, "Report is suspiciously small"

    # Also assert the Final_Report (24-col breakdown) exists
    final_report = tmp_path / "outputs" / "developer" / "Final_Report_developer_2026_02.xlsx"
    assert final_report.exists(), f"Missing final report: {final_report}"


# ═════════════════════════════════════════════════════════════════════════════
# Test 2 — support sub-team bulk run
# ═════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_support_impl_its_bulk_run_end_to_end(
    in_memory_db: AsyncSession, tmp_path: pathlib.Path
) -> None:
    """
    Run the supervisor on the Implementation & I.T.S demo Excel and
    verify the support report lands at the canonical path.
    """
    assert SUPPORT_EXCEL.exists(), f"Missing demo file: {SUPPORT_EXCEL}"
    file_bytes = SUPPORT_EXCEL.read_bytes()

    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        with _multi_context(_all_data_source_patches()):
            summary = await run_supervisor(
                file_bytes=file_bytes,
                raw_team_input="Implementation & I.T.S",  # fuzzy-resolves to impl_its
                year=2026,
                month=4,
                db=in_memory_db,
            )
    finally:
        os.chdir(cwd)

    # ── Assertions on the supervisor summary ────────────────────────────
    assert summary["team"] == "impl_its", f"Expected 'impl_its', got {summary['team']!r}"
    assert summary["year"] == 2026
    assert summary["month"] == 4
    assert summary["status"] in ("success", "partial"), (
        f"Expected success/partial, got {summary['status']!r}: {summary.get('errors')}"
    )
    # The prefetched data is empty under mocks, so per-employee scores
    # are computed from defaults (0 hours, no tickets, etc.).  We assert
    # only that all 23 rows were processed.
    assert summary["processed_count"] == 23, (
        f"Expected 23 processed, got {summary['processed_count']}: {summary.get('errors')}"
    )

    # ── Report file: outputs/support/Support_Final_Report_impl_its_2026_04.xlsx
    expected = tmp_path / "outputs" / "support" / "Support_Final_Report_impl_its_2026_04.xlsx"
    assert expected.exists(), f"Missing report: {expected}"
    assert expected.stat().st_size > 1000, "Report is suspiciously small"


# ═════════════════════════════════════════════════════════════════════════════
# Helper — nested context manager
# ═════════════════════════════════════════════════════════════════════════════


import contextlib


@contextlib.contextmanager
def _multi_context(managers: list[Any]):
    """Enter a list of context managers, exit in reverse order on error."""
    # Manual nesting so any individual __enter__ failure raises cleanly
    entered: list[Any] = []
    try:
        for m in managers:
            entered.append(m.__enter__())
        yield entered
    finally:
        for m in reversed(managers):
            try:
                m.__exit__(None, None, None)
            except Exception:
                pass
