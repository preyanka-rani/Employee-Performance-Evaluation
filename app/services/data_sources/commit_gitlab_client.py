"""
app/services/data_sources/commit_gitlab_client.py
──────────────────────────────────────────────────
Commit-based GitLab data source.

Strategy
────────
1. PostgreSQL → find project_ids where the developer had push-events this month
               (events.action = 5 means "pushed"; fast indexed query)
2. GitLab REST API → per project, list commits filtered by author email + date range
3. GitLab REST API → per commit sha, fetch the file-level diff
4. Dedup → commits are returned newest-first; first occurrence of a file wins
           (keeps the most recent change when a file was touched many times)
5. Filter → skip merge commits, binary files, generated/vendor paths

Returns one CommitBundle per project — all unique file diffs aggregated for
the month.  The downstream AI analysis receives the same MRDiff objects that
the MR-based pipeline uses, so no other layer needs to change.

Why commit-based beats MR-based for individual developer scoring
────────────────────────────────────────────────────────────────
• Exact attribution: every file in the result was committed by THIS developer.
• Works even when developers push directly without raising an MR.
• Month-bounded: only code committed in the evaluation period is scored.
• Merge commits (which contain other people's code) are excluded.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from app.core.config import get_settings
from app.core.logging_config import get_logger
from app.services.data_sources.gitlab_client import MRDiff, _is_ignored, _truncate_diff

logger = get_logger(__name__)
settings = get_settings()

# ── Merge-commit title prefixes ──────────────────────────────────────────────
_MERGE_PREFIXES: tuple[str, ...] = (
    "Merge branch ",
    "Merge remote-tracking branch ",
    "Merged in ",
    "Merge pull request ",
    "Merge tag ",
)


def _is_merge_commit(title: str) -> bool:
    """Return True if this commit's title marks it as an automatic merge."""
    return any(title.startswith(p) for p in _MERGE_PREFIXES)


# ── Data containers ──────────────────────────────────────────────────────────


@dataclass
class CommitBundle:
    """
    Aggregated code changes by one developer in one project for one month.

    Contains deduplicated file diffs (newest commit wins per file).
    Used as the unit of analysis — one CommitBundle → one AI quality score.
    """

    project_id: int
    project_path: str  # "namespace/project"
    commit_count: int
    file_count: int
    period: str  # "YYYY-MM"
    diffs: list[MRDiff] = field(default_factory=list)

    @property
    def analysis_reference(self) -> str:
        """Human-readable label stored in the mr_reference column."""
        return f"{self.project_path} ({self.period}, {self.commit_count} commits)"

    @property
    def analysis_title(self) -> str:
        """Human-readable title stored in the mr_title column."""
        return f"Commit bundle — {self.project_path} [{self.period}]"


# ── Client ───────────────────────────────────────────────────────────────────


class CommitBasedGitLabClient:
    """
    Finds what code a developer authored in a given month by combining:
      • PostgreSQL (GitLab internal DB) for project discovery — fast, no pagination.
      • GitLab REST API (python-gitlab) for commit metadata and file diffs.

    READ-ONLY.  No writes anywhere.
    """

    def __init__(self) -> None:
        self._pg_pool: Any | None = None
        self._gl: Any | None = None

    # ── Pool helpers ─────────────────────────────────────────────────────────

    async def _get_pg_pool(self) -> Any:
        """Lazily create an asyncpg connection pool to the GitLab PostgreSQL DB."""
        if self._pg_pool is None:
            try:
                import asyncpg  # type: ignore[import]
            except ImportError as exc:
                raise RuntimeError(
                    "asyncpg is required for commit-based analysis"
                ) from exc

            self._pg_pool = await asyncpg.create_pool(
                host=settings.gitlab_db_host,
                port=settings.gitlab_db_port,
                database=settings.gitlab_db_name,
                user=settings.gitlab_db_user,
                password=settings.gitlab_db_password,
                min_size=1,
                max_size=5,
                command_timeout=30,
            )
        return self._pg_pool

    def _get_gl(self) -> Any:
        """Lazily create a python-gitlab client (blocking, sync)."""
        if self._gl is None:
            try:
                import gitlab  # type: ignore[import]
            except ImportError as exc:
                raise RuntimeError(
                    "python-gitlab is required for commit-based analysis"
                ) from exc

            self._gl = gitlab.Gitlab(
                url=settings.gitlab_url,
                private_token=settings.gitlab_token,
            )
        return self._gl

    # ── PostgreSQL project discovery ─────────────────────────────────────────

    async def get_developer_projects(
        self,
        username: str,
        year: int,
        month: int,
    ) -> list[dict[str, Any]]:
        """
        Return distinct projects where the developer had push-events this month.

        Uses GitLab's internal ``events`` table (action = 5 → pushed).
        A single indexed query; does not depend on REST API pagination.
        """
        start = datetime(year, month, 1)
        end = datetime(year + 1, 1, 1) if month == 12 else datetime(year, month + 1, 1)

        pool = await self._get_pg_pool()

        query = """
            SELECT DISTINCT
                e.project_id,
                p.path        AS project_path,
                n.path        AS namespace_path
            FROM events e
            JOIN users       u ON e.author_id   = u.id
            JOIN projects    p ON e.project_id  = p.id
            JOIN namespaces  n ON p.namespace_id = n.id
            WHERE u.username = $1
              AND e.action   = 5
              AND e.created_at >= $2
              AND e.created_at <  $3
            ORDER BY e.project_id
        """

        async with pool.acquire() as conn:
            rows = await conn.fetch(query, username, start, end)

        projects = [
            {
                "project_id": row["project_id"],
                "project_path": f"{row['namespace_path']}/{row['project_path']}",
            }
            for row in rows
        ]

        logger.info(
            "commit_projects_found",
            username=username,
            year=year,
            month=month,
            count=len(projects),
        )
        return projects

    # ── REST API helpers (blocking — run in executor) ─────────────────────────

    def _list_commits_sync(
        self,
        project_id: int,
        author_email: str,
        since: str,
        until: str,
    ) -> list[Any]:
        """
        List commits for *project_id* authored by *author_email* in [since, until).
        Runs in a thread executor (python-gitlab is synchronous).
        Returns commits newest-first (GitLab API default).
        """
        try:
            project = self._get_gl().projects.get(project_id)
            commits = project.commits.list(
                author_email=author_email,
                since=since,
                until=until,
                all=True,
            )
            return list(commits)
        except Exception as exc:
            logger.warning("commit_list_failed", project_id=project_id, error=str(exc))
            return []

    def _get_commit_diff_sync(self, project_id: int, sha: str) -> list[dict[str, Any]]:
        """
        Fetch the file-level diff for a single commit sha.
        Runs in a thread executor.
        """
        try:
            project = self._get_gl().projects.get(project_id)
            return project.commits.get(sha).diff()  # type: ignore[return-value]
        except Exception as exc:
            logger.warning("commit_diff_failed", sha=sha, error=str(exc))
            return []

    def _list_commits_with_stats_sync(
        self,
        project_id: int,
        author_email: str,
        since: str,
        until: str,
    ) -> list[Any]:
        """
        List commits with per-commit additions/deletions stats.
        Uses with_stats=True so each commit object includes a stats dict.
        Runs in a thread executor (python-gitlab is synchronous).
        """
        try:
            project = self._get_gl().projects.get(project_id)
            commits = project.commits.list(
                author_email=author_email,
                since=since,
                until=until,
                all=True,
                with_stats=True,
            )
            return list(commits)
        except Exception as exc:
            logger.warning(
                "commit_stats_list_failed", project_id=project_id, error=str(exc)
            )
            return []

    async def get_developer_line_stats(
        self,
        username: str,
        author_email: str,
        year: int,
        month: int,
    ) -> dict[str, int]:
        """
        Return total lines added and deleted by *author_email* in the given
        month, summed across all projects (merge commits excluded).

        Uses with_stats=True on the commits list endpoint — one REST call per
        project instead of one per commit.
        """
        projects = await self.get_developer_projects(username, year, month)
        if not projects:
            return {"total_additions": 0, "total_deletions": 0}

        start_date = date(year, month, 1)
        end_date = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
        since_str = start_date.isoformat() + "T00:00:00Z"
        until_str = end_date.isoformat() + "T00:00:00Z"

        loop = asyncio.get_event_loop()
        total_additions = 0
        total_deletions = 0

        for proj in projects:
            commits = await loop.run_in_executor(
                None,
                self._list_commits_with_stats_sync,
                proj["project_id"],
                author_email,
                since_str,
                until_str,
            )
            for commit in commits:
                if _is_merge_commit(getattr(commit, "title", "") or ""):
                    continue
                stats = getattr(commit, "stats", None)
                if stats is None:
                    continue
                if isinstance(stats, dict):
                    total_additions += int(stats.get("additions", 0) or 0)
                    total_deletions += int(stats.get("deletions", 0) or 0)
                else:
                    total_additions += int(getattr(stats, "additions", 0) or 0)
                    total_deletions += int(getattr(stats, "deletions", 0) or 0)

        logger.info(
            "commit_line_stats",
            username=username,
            year=year,
            month=month,
            total_additions=total_additions,
            total_deletions=total_deletions,
        )
        return {"total_additions": total_additions, "total_deletions": total_deletions}

    # ── Main public method ───────────────────────────────────────────────────

    async def get_developer_commit_bundles(
        self,
        username: str,
        author_email: str,
        year: int,
        month: int,
    ) -> list[CommitBundle]:
        """
        High-level entry point.

        1. Discover projects via PostgreSQL.
        2. For each project, fetch commits from the GitLab REST API,
           filtered by ``author_email`` and the target month.
        3. Skip merge commits.
        4. Fetch the diff for every commit.
        5. Deduplicate: commits are newest-first, so the first diff seen
           for each file is the most recent change — keep it, discard older.
        6. Return one CommitBundle per project (empty projects omitted).
        """
        projects = await self.get_developer_projects(username, year, month)
        if not projects:
            logger.info(
                "commit_no_projects",
                username=username,
                year=year,
                month=month,
            )
            return []

        # ISO-8601 boundaries for the REST API
        start_date = date(year, month, 1)
        end_date = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
        since_str = start_date.isoformat() + "T00:00:00Z"
        until_str = end_date.isoformat() + "T00:00:00Z"

        loop = asyncio.get_event_loop()
        bundles: list[CommitBundle] = []

        for proj in projects:
            project_id: int = proj["project_id"]
            project_path: str = proj["project_path"]

            # 1. List commits for this project / author
            commits: list[Any] = await loop.run_in_executor(
                None,
                self._list_commits_sync,
                project_id,
                author_email,
                since_str,
                until_str,
            )

            # 2. Drop merge commits (they contain other people's code)
            commits = [c for c in commits if not _is_merge_commit(c.title or "")]
            if not commits:
                continue

            logger.info(
                "commit_project_commits",
                project=project_path,
                commit_count=len(commits),
            )

            # 3. Collect diffs, deduplicate by file path.
            #    Commits arrive newest-first → first occurrence of a file wins.
            latest_diffs: dict[str, MRDiff] = {}

            for commit in commits:
                diff_files: list[dict[str, Any]] = await loop.run_in_executor(
                    None,
                    self._get_commit_diff_sync,
                    project_id,
                    commit.id,
                )

                for d in diff_files:
                    file_path: str = d.get("new_path") or d.get("old_path") or ""
                    if not file_path:
                        continue
                    if _is_ignored(file_path):
                        continue
                    # Already recorded a newer diff for this file
                    if file_path in latest_diffs:
                        continue

                    content: str = d.get("diff") or ""
                    if not content.strip() or len(content.strip()) < 20:
                        continue

                    latest_diffs[file_path] = MRDiff(
                        file_path=file_path,
                        diff_content=_truncate_diff(content),
                    )

            if not latest_diffs:
                continue

            diffs = list(latest_diffs.values())
            bundles.append(
                CommitBundle(
                    project_id=project_id,
                    project_path=project_path,
                    commit_count=len(commits),
                    file_count=len(diffs),
                    period=f"{year}-{month:02d}",
                    diffs=diffs,
                )
            )

        logger.info(
            "commit_bundles_ready",
            username=username,
            project_count=len(bundles),
            total_files=sum(b.file_count for b in bundles),
        )
        return bundles

    async def close(self) -> None:
        """Release the PostgreSQL connection pool."""
        if self._pg_pool is not None:
            await self._pg_pool.close()
            self._pg_pool = None
