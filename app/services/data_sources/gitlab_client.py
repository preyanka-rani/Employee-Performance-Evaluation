"""
app/services/data_sources/gitlab_client.py
───────────────────────────────────────────
Read-only GitLab client for fetching Merge Request data and diffs.

Design decisions:
- Uses python-gitlab in async-friendly wrapper (run_in_executor for blocking calls).
- READ-ONLY: only list/get operations, never create/update/delete.
- Filters out non-code files (migrations, lock files, generated code, vendor, minified).
- Retries transient HTTP errors with exponential back-off via tenacity.
"""

import asyncio
from dataclasses import dataclass, field
from datetime import date

import gitlab
from tenacity import retry, stop_after_attempt, wait_exponential

from app.core.config import get_settings
from app.core.logging_config import get_logger

settings = get_settings()
logger = get_logger(__name__)

# ── File patterns to IGNORE when analysing diffs ─────────────────────────────
_IGNORED_PATH_PATTERNS: tuple[str, ...] = (
    # Database migrations
    "migrations/",
    "migrate/",
    "/migration",
    # Dependency lockfiles
    "package-lock.json",
    "yarn.lock",
    "composer.lock",
    "Pipfile.lock",
    "poetry.lock",
    "Cargo.lock",
    # Generated / vendor / minified
    "vendor/",
    "node_modules/",
    ".min.js",
    ".min.css",
    ".generated.",
    "__generated__",
    # Build artefacts
    "dist/",
    "build/",
    ".pyc",
    "__pycache__",
)

# ── Maximum diff content to send to AI (tokens) ──────────────────────────────
_MAX_DIFF_CHARS = 12_000


@dataclass
class MRDiff:
    """Filtered diff for a single file in an MR."""

    file_path: str
    diff_content: str


@dataclass
class MergeRequestData:
    """All data needed to analyse one MR."""

    mr_id: int
    project_id: int
    project_path: str
    mr_reference: str  # "group/project!42"
    title: str
    author_username: str
    merged_at: str
    diffs: list[MRDiff] = field(default_factory=list)


def _is_ignored(file_path: str) -> bool:
    """Return True if the file should be excluded from AI analysis."""
    lower = file_path.lower()
    return any(pattern in lower for pattern in _IGNORED_PATH_PATTERNS)


def _truncate_diff(diff: str) -> str:
    """Truncate very large diffs to stay within model context limits."""
    if len(diff) > _MAX_DIFF_CHARS:
        return diff[:_MAX_DIFF_CHARS] + "\n... [diff truncated]"
    return diff


class GitLabClient:
    """
    Async-friendly wrapper around python-gitlab.

    All blocking python-gitlab calls are offloaded to a thread pool via
    asyncio.get_event_loop().run_in_executor so the FastAPI event loop
    is never blocked.
    """

    def __init__(self) -> None:
        self._gl = gitlab.Gitlab(
            url=settings.gitlab_url,
            private_token=settings.gitlab_token,
        )

    def _sync_authenticate(self) -> None:
        self._gl.auth()

    async def authenticate(self) -> None:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._sync_authenticate)
        logger.info("gitlab_auth_ok", url=settings.gitlab_url)

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        reraise=True,
    )
    async def get_merged_mrs_for_user(
        self,
        gitlab_username: str,
        year: int,
        month: int,
    ) -> list[MergeRequestData]:
        """
        Fetch all MRs merged by a specific user in the given year/month.

        Scans all projects under the configured group.
        """
        start_date = date(year, month, 1)
        # End date: first day of next month
        if month == 12:
            end_date = date(year + 1, 1, 1)
        else:
            end_date = date(year, month + 1, 1)

        loop = asyncio.get_event_loop()

        def _sync_fetch() -> list[MergeRequestData]:
            results: list[MergeRequestData] = []

            group = self._gl.groups.get(settings.gitlab_group_id)
            projects = group.projects.list(all=True, include_subgroups=True)

            for project_stub in projects:
                project = self._gl.projects.get(project_stub.id)
                mrs = project.mergerequests.list(
                    state="merged",
                    author_username=gitlab_username,
                    merged_after=start_date.isoformat(),
                    merged_before=end_date.isoformat(),
                    all=True,
                )
                for mr in mrs:
                    diffs = self._extract_diffs(project, mr)
                    if diffs:
                        results.append(
                            MergeRequestData(
                                mr_id=mr.iid,
                                project_id=project.id,
                                project_path=project.path_with_namespace,
                                mr_reference=f"{project.path_with_namespace}!{mr.iid}",
                                title=mr.title,
                                author_username=gitlab_username,
                                merged_at=str(mr.merged_at),
                                diffs=diffs,
                            )
                        )
            return results

        return await loop.run_in_executor(None, _sync_fetch)

    def _extract_diffs(self, project, mr) -> list[MRDiff]:
        """
        Fetch and filter diffs for a single MR.
        Ignores migration files, lock files, generated code, vendor, and minified assets.
        """
        try:
            raw_diffs = mr.diffs.list()
        except Exception as exc:
            logger.warning("mr_diff_fetch_error", mr_id=mr.iid, error=str(exc))
            return []

        filtered: list[MRDiff] = []
        for d in raw_diffs:
            for change in d.diffs:
                file_path = change.get("new_path") or change.get("old_path", "")
                if _is_ignored(file_path):
                    continue
                diff_content = change.get("diff", "")
                if not diff_content.strip():
                    continue
                filtered.append(
                    MRDiff(
                        file_path=file_path,
                        diff_content=_truncate_diff(diff_content),
                    )
                )

        return filtered
