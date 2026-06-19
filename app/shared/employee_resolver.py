"""
app/shared/employee_resolver.py
───────────────────────────────
Resolve missing employee_ids from the Excel upload via MySQL CRM.

Both the legacy developer flow (using MySQLCRMClient) and the legacy support
flow (using SupportCRMClient) issued a `users` table lookup. This module
unifies that lookup so the orchestrator can do it once, regardless of team.
"""

from __future__ import annotations

from app.core.logging_config import get_logger
from app.shared.data_sources.mysql_client import MySQLCRMClient
from app.shared.excel_parser.row_schema import CanonicalRow

logger = get_logger(__name__)


async def resolve_missing_employee_ids(
    rows: list[CanonicalRow],
) -> int:
    """
    For every row whose ``employee_id`` is empty, look up the ID by email
    from the CRM `users` table and patch the row in place.

    Returns the number of rows that were resolved.

    Rows that cannot be resolved keep their empty ``employee_id``; the caller
    is responsible for filtering or warning the user about them.
    """
    missing_emails = [r.employee_email for r in rows if not r.employee_id]
    if not missing_emails:
        return 0

    client = MySQLCRMClient()
    try:
        resolved = await client.get_employee_ids_by_emails(missing_emails)
    finally:
        await client.close()

    patched = 0
    still_missing: list[str] = []
    for row in rows:
        if not row.employee_id:
            found = resolved.get(row.employee_email)
            if found:
                row.employee_id = found
                patched += 1
                logger.info(
                    "employee_id_resolved_from_mysql",
                    email=row.employee_email,
                    employee_id=found,
                )
            else:
                still_missing.append(row.employee_email)
                logger.warning(
                    "employee_id_not_found_in_mysql", email=row.employee_email
                )
    if still_missing:
        logger.error(
            "employee_id_lookup_failed",
            emails=still_missing,
            message="Some rows will be dropped downstream",
        )
    return patched
