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


def _count_diff_lines(diffs: list[MRDiff]) -> tuple[int, int]:
    """
    Count approximate lines added and deleted from a list of unified diffs.

    Returns (lines_added, lines_deleted).  The counts come from the stored
    diff content (which may be truncated per file at 12 k chars), so very
    large files will be under-counted.  The result is good enough for the
    report — it mirrors what ``git diff --stat`` would show for most commits.
    """
    added = 0
    deleted = 0
    for d in diffs:
        for line in d.diff_content.splitlines():
            if line.startswith("+") and not line.startswith("+++"):
                added += 1
            elif line.startswith("-") and not line.startswith("---"):
                deleted += 1
    return added, deleted


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
    lines_added: int = field(default=0)
    lines_deleted: int = field(default=0)

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

    # ── GitLab identity resolution (multi-step) ───────────────────────────────

    def _resolve_gitlab_identity_sync(
        self, corporate_email: str
    ) -> dict[str, Any] | None:
        """
        Resolve a corporate email to a real GitLab profile using multi-step matching.

        Steps (in order of confidence):
          1. Exact ``email`` field match
          2. Exact ``public_email`` field match
          3. ``username`` equals email prefix (e.g. "md.mehedi" == "md.mehedi")
          4. Email prefix is a substring of ``email`` or ``username``
          Fallback: single-result search → accept that result regardless

        Returns a dict with keys: id, username, email, name — or None.
        """
        email_prefix = corporate_email.split("@")[0].lower()

        try:
            results = list(
                self._get_gl().users.list(
                    search=corporate_email, get_all=False, per_page=10
                )
            )
        except Exception as exc:
            logger.warning(
                "gitlab_user_search_failed", email=corporate_email, error=str(exc)
            )
            return None

        if not results:
            return None

        def _profile(user: Any) -> dict[str, Any]:
            return {
                "id": user.id,
                "username": getattr(user, "username", "") or "",
                "email": getattr(user, "email", "") or "",
                "public_email": getattr(user, "public_email", "") or "",
                "name": getattr(user, "name", "") or "",
            }

        for user in results:
            p = _profile(user)
            u_email = p["email"].lower()
            u_pub = p["public_email"].lower()
            u_uname = p["username"].lower()
            target = corporate_email.lower()

            # Step 1 – exact email
            if u_email == target:
                logger.info(
                    "gitlab_identity_exact_email",
                    email=corporate_email,
                    username=p["username"],
                )
                return p
            # Step 2 – public_email
            if u_pub == target:
                logger.info(
                    "gitlab_identity_public_email",
                    email=corporate_email,
                    username=p["username"],
                )
                return p
            # Step 3 – username == prefix (exact)
            if u_uname == email_prefix:
                logger.info(
                    "gitlab_identity_username_prefix",
                    email=corporate_email,
                    username=p["username"],
                )
                return p
            # Step 4 – prefix is substring of email or username
            if email_prefix in u_email or email_prefix in u_uname:
                logger.info(
                    "gitlab_identity_prefix_substring",
                    email=corporate_email,
                    username=p["username"],
                )
                return p

        # Fallback – only one candidate
        if len(results) == 1:
            p = _profile(results[0])
            logger.info(
                "gitlab_identity_fallback_single",
                email=corporate_email,
                username=p["username"],
            )
            return p

        logger.warning(
            "gitlab_identity_unresolved", email=corporate_email, candidates=len(results)
        )
        return None

    @staticmethod
    def _build_author_matches(
        corporate_email: str,
        gitlab_identity: dict[str, Any] | None,
    ) -> set[str]:
        """
        Build the full set of lowercase identifiers used for commit attribution.

        Includes: corporate email, gitlab email, gitlab username,
                  email prefix, display name.
        """
        matches: set[str] = {corporate_email.lower()}
        email_prefix = corporate_email.split("@")[0].lower()
        matches.add(email_prefix)

        if gitlab_identity:
            for key in ("username", "email", "public_email", "name"):
                val = (gitlab_identity.get(key) or "").strip().lower()
                if val:
                    matches.add(val)

        # Remove empty strings just in case
        matches.discard("")
        return matches

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

    async def get_developer_projects_extended(
        self,
        username_candidates: list[str],
        corporate_email: str,
        year: int,
        month: int,
    ) -> list[dict[str, Any]]:
        """
        Extended project discovery — matches on username OR email in the GitLab
        events table.  Accepts multiple username candidates (e.g. resolved
        GitLab username + fallback employee_id) so we never miss a developer
        whose username doesn't exactly match what's stored in the system.
        """
        start = datetime(year, month, 1)
        end = datetime(year + 1, 1, 1) if month == 12 else datetime(year, month + 1, 1)

        pool = await self._get_pg_pool()

        # Filter out empty / None values
        candidates = [c for c in username_candidates if c]

        query = """
            SELECT DISTINCT
                e.project_id,
                p.path        AS project_path,
                n.path        AS namespace_path
            FROM events e
            JOIN users       u ON e.author_id   = u.id
            JOIN projects    p ON e.project_id  = p.id
            JOIN namespaces  n ON p.namespace_id = n.id
            WHERE (
                u.username = ANY($1)
                OR u.email = $2
                OR u.public_email = $2
            )
              AND e.action   = 5
              AND e.created_at >= $3
              AND e.created_at <  $4
            ORDER BY e.project_id
        """

        async with pool.acquire() as conn:
            rows = await conn.fetch(query, candidates, corporate_email, start, end)

        projects = [
            {
                "project_id": row["project_id"],
                "project_path": f"{row['namespace_path']}/{row['project_path']}",
            }
            for row in rows
        ]

        logger.info(
            "commit_projects_extended",
            username_candidates=candidates,
            corporate_email=corporate_email,
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
                author=author_email,
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
            # get_all=True fetches all paginated pages (default cap is 20 items)
            return project.commits.get(sha).diff(get_all=True)  # type: ignore[return-value]
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
                author=author_email,
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

    def _list_commits_multi_match_sync(
        self,
        project_id: int,
        email_candidates: set[str],
        author_matches: set[str],
        since: str,
        until: str,
    ) -> list[Any]:
        """
        Fetch commits for a project using multi-step author matching.

        Strategy (in order):
          1. Try each email candidate via the GitLab ``author`` filter
             (fast — server-side filtering; GitLab matches by name OR email).
          2. If still no commits found, fetch ALL commits for the period and
             filter client-side against ``author_matches`` by checking
             author_email, author_name, committer_email, committer_name.
             (Handles cases where the developer uses an unregistered git email.)

        Results are deduplicated by commit SHA.
        """
        try:
            project = self._get_gl().projects.get(project_id)
        except Exception as exc:
            logger.warning("project_get_failed", project_id=project_id, error=str(exc))
            return []

        seen: dict[str, Any] = {}

        # ── Step 1: server-side filter per email candidate ────────────────────
        for candidate_email in email_candidates:
            try:
                commits = project.commits.list(
                    author=candidate_email,
                    since=since,
                    until=until,
                    all=True,
                )
                for c in commits:
                    if c.id not in seen:
                        seen[c.id] = c
            except Exception as exc:
                logger.warning(
                    "commit_list_email_failed",
                    project_id=project_id,
                    email=candidate_email,
                    error=str(exc),
                )

        if seen:
            return list(seen.values())

        # ── Step 2: client-side fallback (no commits found via email) ─────────
        logger.info(
            "commit_clientside_fallback",
            project_id=project_id,
            email_candidates=list(email_candidates),
        )
        try:
            all_commits = project.commits.list(since=since, until=until, all=True)
        except Exception as exc:
            logger.warning(
                "commit_list_all_failed", project_id=project_id, error=str(exc)
            )
            return []

        for c in all_commits:
            if c.id in seen:
                continue
            c_fields = [
                (getattr(c, "author_email", "") or "").lower(),
                (getattr(c, "author_name", "") or "").lower(),
                (getattr(c, "committer_email", "") or "").lower(),
                (getattr(c, "committer_name", "") or "").lower(),
            ]
            # Match: any token in author_matches appears IN any commit field
            if any(
                any(token in field for token in author_matches)
                for field in c_fields
                if field
            ):
                seen[c.id] = c

        logger.info(
            "commit_clientside_matched",
            project_id=project_id,
            count=len(seen),
        )
        return list(seen.values())

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

        Now uses the extended project discovery (email + multiple username
        candidates) so commits are found even when the stored username doesn't
        match the actual GitLab username.
        """
        loop = asyncio.get_event_loop()

        # Resolve identity for accurate project discovery
        gitlab_identity: dict[str, Any] | None = await loop.run_in_executor(
            None, self._resolve_gitlab_identity_sync, author_email
        )
        resolved_username = gitlab_identity.get("username") if gitlab_identity else None

        username_candidates: list[str] = []
        if resolved_username:
            username_candidates.append(resolved_username)
        if username and username not in username_candidates:
            username_candidates.append(username)
        email_prefix = author_email.split("@")[0]
        if email_prefix not in username_candidates:
            username_candidates.append(email_prefix)

        projects = await self.get_developer_projects_extended(
            username_candidates=username_candidates,
            corporate_email=author_email,
            year=year,
            month=month,
        )
        if not projects:
            return {"total_additions": 0, "total_deletions": 0}

        start_date = date(year, month, 1)
        end_date = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
        since_str = start_date.isoformat() + "T00:00:00Z"
        until_str = end_date.isoformat() + "T00:00:00Z"

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
        High-level entry point with multi-step GitLab user identification.

        Pipeline
        ────────
        1. Resolve identity  → call GitLab Users API with ``author_email``
                               and apply multi-step profile matching to find
                               the actual GitLab username + email.
        2. Build author set  → {corporate_email, gitlab_email, gitlab_username,
                                email_prefix, display_name}
        3. Discover projects → PostgreSQL events table using all username
                               candidates + corporate_email (extended query).
        4. Per project       → fetch commits via server-side email filter for
                               every email in the author set; fall back to
                               full-project client-side scan if nothing found.
        5. Drop merge commits; deduplicate diffs newest-first.
        6. Return one CommitBundle per project (empty projects omitted).
        """
        loop = asyncio.get_event_loop()

        # ── Step 1: Resolve GitLab identity ──────────────────────────────────
        gitlab_identity: dict[str, Any] | None = await loop.run_in_executor(
            None, self._resolve_gitlab_identity_sync, author_email
        )

        resolved_username: str | None = (
            gitlab_identity.get("username") if gitlab_identity else None
        )
        logger.info(
            "gitlab_identity_resolved",
            corporate_email=author_email,
            resolved_username=resolved_username,
            fallback_username=username,
        )

        # ── Step 2: Build author match set ────────────────────────────────────
        author_matches = self._build_author_matches(author_email, gitlab_identity)
        email_candidates = {m for m in author_matches if "@" in m}

        logger.info(
            "author_matches_built",
            email=author_email,
            matches=sorted(author_matches),
        )

        # ── Step 3: Discover projects (extended — username + email) ───────────
        username_candidates: list[str] = []
        if resolved_username:
            username_candidates.append(resolved_username)
        # Always include the provided username as a fallback (employee_id etc.)
        if username and username not in username_candidates:
            username_candidates.append(username)
        # Also add the email prefix as a username candidate
        email_prefix = author_email.split("@")[0]
        if email_prefix not in username_candidates:
            username_candidates.append(email_prefix)

        projects = await self.get_developer_projects_extended(
            username_candidates=username_candidates,
            corporate_email=author_email,
            year=year,
            month=month,
        )

        if not projects:
            logger.info(
                "commit_no_projects",
                username_candidates=username_candidates,
                corporate_email=author_email,
                year=year,
                month=month,
            )
            return []

        # ISO-8601 boundaries for the REST API
        start_date = date(year, month, 1)
        end_date = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
        since_str = start_date.isoformat() + "T00:00:00Z"
        until_str = end_date.isoformat() + "T00:00:00Z"

        bundles: list[CommitBundle] = []

        for proj in projects:
            project_id: int = proj["project_id"]
            project_path: str = proj["project_path"]

            # ── Step 4: Fetch commits (multi-email + client-side fallback) ────
            commits: list[Any] = await loop.run_in_executor(
                None,
                self._list_commits_multi_match_sync,
                project_id,
                email_candidates,
                author_matches,
                since_str,
                until_str,
            )

            # Drop merge commits (contain other people's code)
            commits = [
                c
                for c in commits
                if not _is_merge_commit(getattr(c, "title", "") or "")
            ]
            if not commits:
                continue

            logger.info(
                "commit_project_commits",
                project=project_path,
                commit_count=len(commits),
            )

            # ── Step 5: Collect diffs, deduplicate by file path ───────────────
            # Commits may arrive in any order; sort newest-first by authored_date
            commits.sort(
                key=lambda c: getattr(c, "authored_date", "") or "",
                reverse=True,
            )
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
                    if not file_path or _is_ignored(file_path):
                        continue
                    if file_path in latest_diffs:
                        continue  # newer diff already recorded

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
            added, deleted = _count_diff_lines(diffs)
            bundles.append(
                CommitBundle(
                    project_id=project_id,
                    project_path=project_path,
                    commit_count=len(commits),
                    file_count=len(diffs),
                    period=f"{year}-{month:02d}",
                    diffs=diffs,
                    lines_added=added,
                    lines_deleted=deleted,
                )
            )

        logger.info(
            "commit_bundles_ready",
            username_candidates=username_candidates,
            project_count=len(bundles),
            total_files=sum(b.file_count for b in bundles),
        )
        return bundles

    async def close(self) -> None:
        """Release the PostgreSQL connection pool."""
        if self._pg_pool is not None:
            await self._pg_pool.close()
            self._pg_pool = None
