"""
app/shared/data_sources/postgresql_gitlab_client.py
────────────────────────────────────────────────────
Direct PostgreSQL client for GitLab's internal database (`gitlabhq_production`).

Why use this instead of the REST API client?
  - No GITLAB_GROUP_ID required — queries by username directly.
  - Orders of magnitude faster for large instances (single query vs. iterating
    every project in a group via paginated REST calls).
  - No API rate limiting.

READ-ONLY — only SELECT statements, no writes.

Requires:
  GITLAB_DB_HOST, GITLAB_DB_PORT, GITLAB_DB_NAME,
  GITLAB_DB_USER, GITLAB_DB_PASSWORD  in .env

GitLab DB tables used:
  merge_requests         – MR metadata (title, state, merged_at, author_id)
  users                  – Maps username → id
  projects               – Project path and namespace
  namespaces             – Group / user namespace path
  merge_request_diffs    – Diff versions for each MR (latest = MAX(id))
  merge_request_diff_files – Per-file unified diff content
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any

from app.core.config import get_settings
from app.core.logging_config import get_logger
from app.shared.data_sources.gitlab_client import (
    MRDiff,
    MergeRequestData,
    _IGNORED_PATH_PATTERNS,
    _truncate_diff,
)

logger = get_logger(__name__)
settings = get_settings()


def _is_ignored(file_path: str) -> bool:
    lower = file_path.lower()
    return any(pattern in lower for pattern in _IGNORED_PATH_PATTERNS)


class PostgreSQLGitLabClient:
    """
    Queries GitLab's PostgreSQL database directly.

    Connection is created lazily on first use and reused across calls.
    Uses asyncpg for non-blocking I/O.
    """

    def __init__(self) -> None:
        self._pool: Any | None = None  # asyncpg.Pool

    async def _get_pool(self) -> Any:
        """Lazily create the asyncpg connection pool."""
        if self._pool is None:
            try:
                import asyncpg  # type: ignore[import]
            except ImportError as exc:
                raise RuntimeError(
                    "asyncpg is required for direct GitLab DB access. "
                    "Install it with: pip install asyncpg"
                ) from exc

            self._pool = await asyncpg.create_pool(
                host=settings.gitlab_db_host,
                port=settings.gitlab_db_port,
                database=settings.gitlab_db_name,
                user=settings.gitlab_db_user,
                password=settings.gitlab_db_password,
                min_size=1,
                max_size=5,
                command_timeout=30,
            )
            logger.info(
                "gitlab_pg_pool_created",
                host=settings.gitlab_db_host,
                db=settings.gitlab_db_name,
            )
        return self._pool

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    async def get_merged_mrs_for_user(
        self,
        gitlab_username: str,
        year: int,
        month: int,
    ) -> list[MergeRequestData]:
        """
        Return all MRs merged by `gitlab_username` in the given month.
        Diffs are fetched in a second query and filtered in-memory.
        """
        start_date = date(year, month, 1)
        end_date = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)

        pool = await self._get_pool()

        # ── 1. Fetch MR metadata ─────────────────────────────────────────────
        namespace_filter = ""
        params: list[Any] = [
            gitlab_username,
            datetime(start_date.year, start_date.month, start_date.day),
            datetime(end_date.year, end_date.month, end_date.day),
        ]

        if settings.gitlab_db_namespace:
            namespace_filter = "AND n.path = $4"
            params.append(settings.gitlab_db_namespace)

        mr_query = f"""
            SELECT
                mr.id            AS mr_db_id,
                mr.iid           AS mr_iid,
                mr.title         AS title,
                mr.source_project_id AS project_id,
                mmt.merged_at    AS merged_at,
                p.path           AS project_path,
                n.path           AS namespace_path
            FROM merge_requests mr
            JOIN merge_request_metrics mmt ON mmt.merge_request_id = mr.id
            JOIN users u          ON mr.author_id = u.id
            JOIN projects p       ON mr.source_project_id = p.id
            JOIN namespaces n     ON p.namespace_id = n.id
            WHERE u.username = $1
              AND mr.state_id = 3
              AND mmt.merged_at >= $2
              AND mmt.merged_at <  $3
              {namespace_filter}
            ORDER BY mmt.merged_at DESC
        """

        async with pool.acquire() as conn:
            mr_rows = await conn.fetch(mr_query, *params)

        logger.info(
            "pg_gitlab_mrs_fetched",
            username=gitlab_username,
            count=len(mr_rows),
            year=year,
            month=month,
        )

        if not mr_rows:
            return []

        # ── 2. Fetch diffs for each MR ───────────────────────────────────────
        results: list[MergeRequestData] = []

        diff_query = """
            SELECT
                mrdf.new_path,
                mrdf.old_path,
                mrdf.diff
            FROM merge_request_diff_files mrdf
            WHERE mrdf.merge_request_diff_id = (
                SELECT id
                FROM merge_request_diffs
                WHERE merge_request_id = $1
                ORDER BY id DESC
                LIMIT 1
            )
              AND (mrdf.binary IS NULL OR mrdf.binary = false)
              AND mrdf.diff IS NOT NULL
              AND mrdf.diff <> ''
        """

        async with pool.acquire() as conn:
            for row in mr_rows:
                path_with_namespace = f"{row['namespace_path']}/{row['project_path']}"
                mr_reference = f"{path_with_namespace}!{row['mr_iid']}"

                diff_rows = await conn.fetch(diff_query, row["mr_db_id"])

                diffs: list[MRDiff] = []
                for d in diff_rows:
                    file_path = d["new_path"] or d["old_path"] or ""
                    if _is_ignored(file_path):
                        continue
                    content = d["diff"] or ""
                    if not content.strip() or len(content.strip()) < 20:
                        continue
                    diffs.append(
                        MRDiff(
                            file_path=file_path,
                            diff_content=_truncate_diff(content),
                        )
                    )

                if diffs:
                    results.append(
                        MergeRequestData(
                            mr_id=row["mr_iid"],
                            project_id=row["project_id"],
                            project_path=path_with_namespace,
                            mr_reference=mr_reference,
                            title=row["title"] or "",
                            author_username=gitlab_username,
                            merged_at=str(row["merged_at"]),
                            diffs=diffs,
                        )
                    )

        logger.info(
            "pg_gitlab_mrs_with_diffs",
            username=gitlab_username,
            count=len(results),
        )
        return results

    async def test_connection(self) -> dict[str, Any]:
        """
        Verify the connection and return basic stats.
        Call this from the test script to validate credentials.
        """
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            version = await conn.fetchval("SELECT version()")
            mr_count = await conn.fetchval(
                "SELECT COUNT(*) FROM merge_requests WHERE state = 'merged'"
            )
            user_count = await conn.fetchval(
                "SELECT COUNT(*) FROM users WHERE state = 'active'"
            )

        return {
            "status": "ok",
            "pg_version": version,
            "total_merged_mrs": mr_count,
            "active_users": user_count,
        }

    async def get_issue_stats(
        self,
        user_email: str,
        year: int,
        month: int,
    ) -> dict[str, int]:
        """
        Return issue assignment, resolution and reopen counts for a developer
        across the full calendar month.

        Mirrors the logic in monthly_report.py get_daily_metrics() but
        aggregated for the entire month in two queries instead of day-by-day.

        Returns:
            total_assigned  – distinct issues where user logged time
            total_resolved  – of those, issues with state_id = 2 (closed)
            total_reopens   – issues that went closed (2) → reopened (3 or 5)
                              with at least 60-second gap between transitions
        """
        start = datetime(year, month, 1)
        end = datetime(year + 1, 1, 1) if month == 12 else datetime(year, month + 1, 1)

        pool = await self._get_pool()

        assign_query = """
            WITH user_issues AS (
                SELECT DISTINCT
                    i.id        AS issue_id,
                    i.state_id
                FROM issues    i
                JOIN timelogs  t ON t.issue_id = i.id
                JOIN users     u ON t.user_id  = u.id
                WHERE u.email    = $1
                  AND t.spent_at >= $2
                  AND t.spent_at <  $3
            )
            SELECT
                COUNT(DISTINCT issue_id)                                    AS total_assigned,
                COUNT(DISTINCT CASE WHEN state_id = 2 THEN issue_id END)   AS total_resolved
            FROM user_issues
        """

        reopen_query = """
            WITH user_issues AS (
                SELECT DISTINCT i.id AS issue_id
                FROM issues    i
                JOIN timelogs  t ON t.issue_id = i.id
                JOIN users     u ON t.user_id  = u.id
                WHERE u.email    = $1
                  AND t.spent_at >= $2
                  AND t.spent_at <  $3
            ),
            state_transitions AS (
                SELECT
                    rse.issue_id,
                    rse.state,
                    rse.created_at,
                    LAG(rse.state) OVER (
                        PARTITION BY rse.issue_id ORDER BY rse.created_at
                    ) AS prev_state,
                    LAG(rse.created_at) OVER (
                        PARTITION BY rse.issue_id ORDER BY rse.created_at
                    ) AS prev_created_at
                FROM resource_state_events rse
                JOIN user_issues ui ON rse.issue_id = ui.issue_id
            )
            SELECT COUNT(DISTINCT issue_id) AS total_reopens
            FROM state_transitions
            WHERE prev_state = 2
              AND state IN (3, 5)
              AND EXTRACT(EPOCH FROM (created_at - prev_created_at)) >= 60
        """

        try:
            async with pool.acquire() as conn:
                assign_row = await conn.fetchrow(assign_query, user_email, start, end)
                reopen_row = await conn.fetchrow(reopen_query, user_email, start, end)
        except Exception as exc:
            logger.warning(
                "pg_issue_stats_failed",
                email=user_email,
                year=year,
                month=month,
                error=str(exc),
            )
            return {"total_assigned": 0, "total_resolved": 0, "total_reopens": 0}

        total_assigned = int(assign_row["total_assigned"] or 0) if assign_row else 0
        total_resolved = int(assign_row["total_resolved"] or 0) if assign_row else 0
        total_reopens = int(reopen_row["total_reopens"] or 0) if reopen_row else 0

        logger.info(
            "pg_issue_stats",
            email=user_email,
            year=year,
            month=month,
            total_assigned=total_assigned,
            total_resolved=total_resolved,
            total_reopens=total_reopens,
        )
        return {
            "total_assigned": total_assigned,
            "total_resolved": total_resolved,
            "total_reopens": total_reopens,
        }
