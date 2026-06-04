"""
app/shared/data_sources/commit_gitlab_client.py
───────────────────────────────────────────────
Commit-based GitLab data source — REST API only, no direct DB connection.

Strategy
────────
1. GitLab Events REST API → /users/{user_id}/events to find all projects where
                             the developer had ANY activity this month
                             (push, MR, comment, review — all event types).
2. GitLab REST API → per project, list commits filtered by author email + date range
                     using ?all=true to search ALL branches (not just default).
3. GitLab REST API → per commit sha, fetch the file-level diff
4. Dedup → commits are returned newest-first; first occurrence of a file wins
           (keeps the most recent change when a file was touched many times)
5. Filter → skip merge commits, binary files, generated/vendor paths

Returns one CommitBundle per project — all unique file diffs aggregated for
the month.  The downstream AI analysis receives the same MRDiff objects that
the MR-based pipeline uses, so no other layer needs to change.

Why REST API events beats PostgreSQL internal DB for project discovery
──────────────────────────────────────────────────────────────────────
• No direct DB access required — uses public GitLab API only.
• Catches ALL activity types (push, MR, comment, review) — none missed.
• Works across all GitLab hosting configurations.
• Identical strategy to the proven developer_report.py script.
• ?all=true in commit queries searches every branch, not just default.
"""

from __future__ import annotations

import asyncio
import calendar
from dataclasses import dataclass, field
from datetime import date
from types import SimpleNamespace
from typing import Any

import requests

from app.core.config import get_settings
from app.core.logging_config import get_logger
from app.shared.data_sources.gitlab_client import MRDiff, _is_ignored, _truncate_diff

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
    Finds what code a developer authored in a given month using the GitLab REST API
    exclusively — no direct database connection required.

    Project discovery uses the GitLab Events REST API so ALL activity types are
    captured (not just push events). Commit fetching uses ?all=true so all branches
    are searched, not only the default branch.

    READ-ONLY.  No writes anywhere.
    """

    def __init__(self) -> None:
        self._gl: Any | None = None

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
        Resolve a corporate email to a real GitLab profile.

        Strategy — mirrors developer_report.py:
          1. Search GitLab users by email (``/users?search=<email>``).
          2. Scan results for an exact email / public_email match or a match
             where the email prefix appears in one of the email fields.
          3. Fallback: take the first search result (same as script's data[0]).

        NOTE: Username-based matching (Pass 2) was intentionally removed.
        An exact username match (e.g. a bot named "dilruba") would intercept
        before the fallback and return the wrong account.  GitLab ranks exact
        email matches first in its search results, so data[0] is the correct
        developer when email-based matching misses (e.g. private email).

        Returns a dict with keys: id, username, email, public_email, name — or None.
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

        # Pass 1 — email-based matching (highest confidence).
        # Checks ONLY email/public_email fields; deliberately ignores username.
        # This prevents a service-account with username == email-prefix
        # (e.g. a bot named "dilruba") from shadowing the real developer
        # "dilrubayasmin095" whose email IS "dilruba@ba-systems.com".
        target = corporate_email.lower()
        for user in results:
            p = _profile(user)
            u_email = p["email"].lower()
            u_pub = p["public_email"].lower()

            # Exact email or public_email match
            if u_email == target:
                logger.info(
                    "gitlab_identity_exact_email",
                    email=corporate_email,
                    username=p["username"],
                )
                return p
            if u_pub == target:
                logger.info(
                    "gitlab_identity_public_email",
                    email=corporate_email,
                    username=p["username"],
                )
                return p
            # Email-prefix appears inside one of the email fields
            # e.g. "dilruba" in "dilruba@ba-systems.com" → True
            if email_prefix and (email_prefix in u_email or email_prefix in u_pub):
                logger.info(
                    "gitlab_identity_email_prefix",
                    email=corporate_email,
                    username=p["username"],
                )
                return p

        # Fallback — mirrors developer_report.py which always uses data[0].
        # GitLab orders results by relevance; searching by exact email puts the
        # real developer first even if their email is private.
        # NOTE: Pass 2 (username-based matching) was removed because an exact
        # username match (e.g. bot named "dilruba") would be returned BEFORE
        # this fallback, defeating the purpose. data[0] is safer and matches
        # what developer_report.py does.
        p = _profile(results[0])
        logger.info(
            "gitlab_identity_fallback_first",
            email=corporate_email,
            username=p["username"],
        )
        return p

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

    # ── Project discovery via GitLab Events REST API ────────────────────────────

    def _find_projects_via_events_sync(
        self,
        gitlab_user_id: int,
        year: int,
        month: int,
    ) -> list[dict[str, Any]]:
        """
        Primary project discovery via the GitLab Events REST API.

        Mirrors developer_report.py's get_user_active_projects() strategy:
        fetches ALL event types for the user in the given month and collects
        every distinct project_id. Then resolves each project_id to a path.

        Catches pushes, MR activity, comments, reviews — any GitLab interaction
        that references a project. More complete than a push-only query.
        """
        raw_url = (settings.gitlab_url or "").rstrip("/")
        base_url = raw_url if raw_url.endswith("/api/v4") else f"{raw_url}/api/v4"
        headers = {"PRIVATE-TOKEN": settings.gitlab_token}

        start_date = date(year, month, 1).strftime("%Y-%m-%d")
        last_day = calendar.monthrange(year, month)[1]
        end_date = date(year, month, last_day).strftime("%Y-%m-%d")

        project_ids: set[int] = set()
        page = 1

        while True:
            try:
                resp = requests.get(
                    f"{base_url}/users/{gitlab_user_id}/events",
                    headers=headers,
                    params={
                        "after": start_date,
                        "before": end_date,
                        "per_page": 100,
                        "page": page,
                    },
                    timeout=30,
                )
                if resp.status_code != 200:
                    logger.warning(
                        "rest_events_bad_status",
                        user_id=gitlab_user_id,
                        status=resp.status_code,
                    )
                    break
                events = resp.json()
            except Exception as exc:
                logger.warning("rest_events_request_failed", error=str(exc))
                break

            if not events:
                break

            for event in events:
                pid = event.get("project_id")
                if pid:
                    project_ids.add(int(pid))

            if len(events) < 100:
                break
            page += 1

        logger.info(
            "events_projects_found",
            user_id=gitlab_user_id,
            count=len(project_ids),
        )

        # Resolve project paths
        gl = self._get_gl()
        results: list[dict[str, Any]] = []
        for proj_id in project_ids:
            try:
                p = gl.projects.get(proj_id)
                results.append(
                    {
                        "project_id": proj_id,
                        "project_path": p.path_with_namespace,
                    }
                )
            except Exception as exc:
                logger.warning(
                    "rest_events_project_get_failed", project_id=proj_id, error=str(exc)
                )
                results.append(
                    {"project_id": proj_id, "project_path": f"project-{proj_id}"}
                )

        return results

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

        NOTE: Fetches ALL commits without a server-side author filter and
        filters client-side by email prefix. GitLab's ``author`` parameter
        is unreliable for email-based filtering.
        """
        email_prefix = author_email.split("@")[0].lower()
        try:
            project = self._get_gl().projects.get(project_id)
            all_commits = project.commits.list(
                since=since,
                until=until,
                query_parameters={"all": "true"},  # all branches
                get_all=True,  # all pages
            )
            result = []
            for c in all_commits:
                a_email = (getattr(c, "author_email", "") or "").lower()
                a_name = (getattr(c, "author_name", "") or "").lower()
                if email_prefix in a_email or email_prefix in a_name:
                    result.append(c)
            return result
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

        Mirrors developer_report.py's get_project_commits_for_developer() exactly:
          • Uses the GitLab REST API directly via ``requests`` (NOT python-gitlab)
            so that ``all=true`` and ``with_stats=true`` are sent as lowercase
            string parameters — python-gitlab converts bool True→"True" which
            GitLab silently ignores, causing wrong counts and zero line stats.
          • Fetches all pages (per_page=100, stops when len(page) < 100).
          • Client-side filter: email_prefix in author_email OR in author_name.

        Returns a list of SimpleNamespace objects with attributes:
          id, title, authored_date, stats (dict with additions/deletions/total)
        """
        raw_url = (settings.gitlab_url or "").rstrip("/")
        base_url = raw_url if raw_url.endswith("/api/v4") else f"{raw_url}/api/v4"
        headers = {"PRIVATE-TOKEN": settings.gitlab_token}
        email_prefix = author_email.split("@")[0].lower()
        results: list[Any] = []
        page = 1

        while True:
            url = f"{base_url}/projects/{project_id}/repository/commits"
            params = {
                "since": since,
                "until": until,
                "all": "true",  # all branches — must be lowercase string
                "with_stats": "true",  # per-commit stats — must be lowercase string
                "per_page": 100,
                "page": page,
            }
            try:
                resp = requests.get(url, headers=headers, params=params, timeout=30)
                if resp.status_code != 200:
                    logger.warning(
                        "commit_stats_api_error",
                        project_id=project_id,
                        status=resp.status_code,
                    )
                    break
                data = resp.json()
            except Exception as exc:
                logger.warning(
                    "commit_stats_list_failed", project_id=project_id, error=str(exc)
                )
                break

            if not data or not isinstance(data, list):
                break

            for commit in data:
                a_email = (commit.get("author_email", "") or "").lower()
                a_name = (commit.get("author_name", "") or "").lower()
                if email_prefix in a_email or email_prefix in a_name:
                    # Wrap in SimpleNamespace so callers use attribute access
                    # (same interface as python-gitlab commit objects)
                    results.append(
                        SimpleNamespace(
                            id=commit.get("id", ""),
                            title=commit.get("title", ""),
                            authored_date=commit.get("authored_date", ""),
                            # stats is a dict: {additions, deletions, total}
                            stats=commit.get("stats"),
                        )
                    )

            if len(data) < 100:
                break
            page += 1

        return results

    def _list_commits_multi_match_sync(
        self,
        project_id: int,
        email_candidates: set[str],
        author_matches: set[str],
        since: str,
        until: str,
        author_email: str = "",
    ) -> list[Any]:
        """
        Fetch commits for a project using client-side author matching.

        Always fetches ALL commits for the period without a server-side author
        filter — GitLab's ``author`` query parameter is unreliable for
        email-based queries (it performs a partial/domain match that returns
        ALL commits from the same email domain, not just the target developer).

        Filters client-side using exactly the same logic as developer_report.py:
            email_prefix in author_email  OR  email_prefix in author_name
        where email_prefix = author_email.split("@")[0].lower().
        Only author_email and author_name are checked (not committer fields).

        Results are deduplicated by commit SHA.
        ``email_candidates`` and ``author_matches`` are kept in the signature
        for API compatibility but are no longer used internally.
        """
        email_prefix = author_email.split("@")[0].lower() if author_email else ""

        try:
            project = self._get_gl().projects.get(project_id)
        except Exception as exc:
            logger.warning("project_get_failed", project_id=project_id, error=str(exc))
            return []

        try:
            all_commits = project.commits.list(
                since=since,
                until=until,
                query_parameters={"all": "true"},  # all branches
                get_all=True,  # all pages
            )
        except Exception as exc:
            logger.warning(
                "commit_list_all_failed", project_id=project_id, error=str(exc)
            )
            return []

        seen: dict[str, Any] = {}
        for c in all_commits:
            if c.id in seen:
                continue
            a_email = (getattr(c, "author_email", "") or "").lower()
            a_name = (getattr(c, "author_name", "") or "").lower()
            # Mirror developer_report.py: email prefix in author_email or author_name
            if email_prefix and (email_prefix in a_email or email_prefix in a_name):
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
        if not gitlab_identity:
            return {"total_additions": 0, "total_deletions": 0}

        gitlab_user_id: int = gitlab_identity["id"]
        projects = await loop.run_in_executor(
            None,
            self._find_projects_via_events_sync,
            gitlab_user_id,
            year,
            month,
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
                               using email-first two-pass matching; falls back
                               to data[0] like developer_report.py.
        2. Discover projects → GitLab Events REST API using the resolved user_id.
                               Captures ALL activity types (push, MR, comment…)
                               so no project is missed.
        3. Per project       → fetch commits with stats via email_prefix filter
                               on author_email + author_name (mirrors script).
                               Uses ?all=true to search ALL branches.
        4. Commit count      → total matched commits INCLUDING merge commits
                               (mirrors developer_report.py exactly).
        5. Lines             → summed from per-commit stats (mirrors script).
        6. AI diffs          → non-merge commits only, deduplicated newest-first.
        7. Return one CommitBundle per project (zero-commit projects omitted).
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

        # ── Step 2: Discover projects via GitLab Events REST API ─────────────
        if not gitlab_identity:
            logger.info(
                "commit_no_identity",
                corporate_email=author_email,
            )
            return []

        gitlab_user_id: int = gitlab_identity["id"]
        projects = await loop.run_in_executor(
            None,
            self._find_projects_via_events_sync,
            gitlab_user_id,
            year,
            month,
        )

        if not projects:
            logger.info(
                "commit_no_projects",
                corporate_email=author_email,
                year=year,
                month=month,
            )
            return []

        # ISO-8601 boundaries for the REST API
        start_date = date(year, month, 1)
        # Commit date range: mirrors developer_report.py exactly
        # Script uses last_day T23:59:59Z, not next-month T00:00:00Z
        last_day_of_month = calendar.monthrange(year, month)[1]
        since_str = start_date.isoformat() + "T00:00:00Z"
        until_str = date(year, month, last_day_of_month).isoformat() + "T23:59:59Z"

        bundles: list[CommitBundle] = []

        for proj in projects:
            project_id: int = proj["project_id"]
            project_path: str = proj["project_path"]

            # ── Step 4: Fetch commits with per-commit stats ───────────────────
            # Uses email_prefix filtering on author_email + author_name — exactly
            # mirrors developer_report.py's commit attribution logic.
            # with_stats=True means additions/deletions come from commit metadata
            # (same as developer_report.py which sums stats per commit).
            commits_all: list[Any] = await loop.run_in_executor(
                None,
                self._list_commits_with_stats_sync,
                project_id,
                author_email,
                since_str,
                until_str,
            )

            # Commit count matches developer_report.py: ALL matched commits
            # including merge commits (script counts them without removal)
            total_commit_count = len(commits_all)
            if total_commit_count == 0:
                continue  # script only includes projects with commit_count > 0

            # Sum line stats from commit metadata — same as developer_report.py
            # which sums commit['stats']['additions'] and ['deletions']
            total_lines_added = 0
            total_lines_deleted = 0
            for c in commits_all:
                stats = getattr(c, "stats", None)
                if stats is None:
                    continue
                if isinstance(stats, dict):
                    total_lines_added += int(stats.get("additions", 0) or 0)
                    total_lines_deleted += int(stats.get("deletions", 0) or 0)
                else:
                    total_lines_added += int(getattr(stats, "additions", 0) or 0)
                    total_lines_deleted += int(getattr(stats, "deletions", 0) or 0)

            logger.info(
                "commit_project_commits",
                project=project_path,
                commit_count=total_commit_count,
                lines_added=total_lines_added,
                lines_deleted=total_lines_deleted,
            )

            # Filter merge commits for AI diff analysis only — merge diffs contain
            # other developers' code and would skew the quality score.
            # Does NOT affect commit_count or lines (already captured above).
            analysis_commits = [
                c
                for c in commits_all
                if not _is_merge_commit(getattr(c, "title", "") or "")
            ]

            # ── Step 5: Collect diffs, deduplicate by file path ───────────────
            # Sort newest-first so the latest version of each file wins
            analysis_commits.sort(
                key=lambda c: getattr(c, "authored_date", "") or "",
                reverse=True,
            )
            latest_diffs: dict[str, MRDiff] = {}

            for commit in analysis_commits:
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

            # Always create a bundle — even if no analyzable diffs were found
            # (e.g. developer only touched config/md files). The downstream
            # workflow assigns score=50 for empty-diff bundles so the
            # developer still receives credit for the work they did.
            diffs = list(latest_diffs.values())
            bundles.append(
                CommitBundle(
                    project_id=project_id,
                    project_path=project_path,
                    commit_count=total_commit_count,  # matches script: incl. merge commits
                    file_count=len(diffs),
                    period=f"{year}-{month:02d}",
                    diffs=diffs,
                    lines_added=total_lines_added,  # matches script: from commit stats
                    lines_deleted=total_lines_deleted,  # matches script: from commit stats
                )
            )

        logger.info(
            "commit_bundles_ready",
            email=author_email,
            project_count=len(bundles),
            total_files=sum(b.file_count for b in bundles),
        )
        return bundles

    async def close(self) -> None:
        """No persistent resources to release."""
        pass
