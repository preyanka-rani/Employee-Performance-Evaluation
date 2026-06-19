"""
app/shared/data_sources/__init__.py
────────────────────────────────────
Team-agnostic read-only data source clients.

All clients in this package contain ZERO team-specific business logic.
They expose raw data (work logs, attendance, GitLab MRs, tickets, etc.)
and let the team workers interpret the data.
"""

from app.shared.data_sources.commit_gitlab_client import CommitBasedGitLabClient
from app.shared.data_sources.gitlab_client import GitLabClient
from app.shared.data_sources.mysql_client import MySQLCRMClient, MySQLHRClient
from app.shared.data_sources.postgresql_gitlab_client import PostgreSQLGitLabClient
from app.shared.data_sources.support_crm_client import SupportCRMClient
from app.shared.data_sources.support_tickets_client import SupportTicketsClient

__all__ = [
    "MySQLCRMClient",
    "MySQLHRClient",
    "GitLabClient",
    "PostgreSQLGitLabClient",
    "CommitBasedGitLabClient",
    "SupportCRMClient",
    "SupportTicketsClient",
]
