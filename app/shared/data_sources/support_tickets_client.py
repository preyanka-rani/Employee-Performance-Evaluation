"""
app/shared/data_sources/support_tickets_client.py
─────────────────────────────────────────────────
Read-only MySQL client for support team ticket data.

Source table: process_list_hist (MySQL CRM DB)
Mirrors: tickets_score.sql from documentation Section 2.1.

All SQL queries are parameterised — no string interpolation of user data.
Only SELECT statements are issued; no DML operations are performed.
"""

from __future__ import annotations

from datetime import date, timedelta
import calendar

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.core.logging_config import get_logger

settings = get_settings()
logger = get_logger(__name__)


class SupportTicketsClient:
    """
    Fetches ticket handling data for support team employees from process_list_hist.

    Methods:
      get_ticket_scores() — per-employee ticket counts and resolution times
    """

    def __init__(self) -> None:
        self._engine = create_async_engine(
            settings.mysql_crm_dsn,
            pool_size=1,
            max_overflow=0,
            pool_pre_ping=True,
            echo=False,
        )
        self._session_factory = async_sessionmaker(
            bind=self._engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )

    async def close(self) -> None:
        await self._engine.dispose()

    # ──────────────────────────────────────────────────────────────────────────
    # Public API
    # ──────────────────────────────────────────────────────────────────────────

    async def get_ticket_scores(
        self,
        employee_ids: list[str],
        year: int,
        month: int,
    ) -> list[dict]:
        """
        Return ticket count and resolution speed per employee for the given month.

        Mirrors tickets_score.sql from documentation Section 2.1.

        Score tiers computed here in Python (see formulas.py), but the raw
        aggregates are returned so callers can apply or override tiers.

        Returns list of dicts:
          {user_email, assigned_parson, total_tickets, average_taken_days}
        """
        if not employee_ids:
            return []

        # Build date range for the month
        start_date = date(year, month, 1)
        last_day = calendar.monthrange(year, month)[1]
        end_date = date(year, month, last_day)

        placeholders = ", ".join(f":emp_{i}" for i in range(len(employee_ids)))
        params: dict = {f"emp_{i}": eid for i, eid in enumerate(employee_ids)}
        params["start_date"] = start_date.isoformat()
        params["end_date"] = f"{end_date.isoformat()} 23:59:59"

        # Mirrors tickets_score.sql exactly:
        # UNION ALL of two subqueries:
        #   1. status_id = 23  (Completed)  — with access_role = 'Employee' filter
        #   2. status_id = 4               — without access_role filter
        # Reference: python-docker_v3_others_Imp_support/sql/mysql/tickets_score.sql
        sql = text(f"""
            SELECT
                user_email,
                assigned_parson,
                SUM(tickets)                  AS total_tickets,
                ROUND(AVG(avg_taken_days), 2) AS average_taken_days
            FROM (
                SELECT
                    u.user_email,
                    CONCAT_WS(
                        ' ',
                        u.user_first_name,
                        u.user_middle_name,
                        u.user_last_name
                    ) AS assigned_parson,
                    COUNT(plh.tracking_no) AS tickets,
                    AVG(
                        IFNULL(DATEDIFF(plh.updated_at, plh.created_at), 0)
                    ) AS avg_taken_days
                FROM process_list_hist plh
                LEFT JOIN users u
                    ON plh.updated_by = u.id
                    AND u.user_status = 'active'
                    AND u.access_role = 'Employee'
                WHERE plh.updated_at BETWEEN :start_date AND :end_date
                  AND plh.process_type = 2
                  AND plh.status_id = 23
                  AND u.employee_id IN ({placeholders})
                GROUP BY u.user_email, assigned_parson

                UNION ALL

                SELECT
                    u.user_email,
                    CONCAT_WS(
                        ' ',
                        u.user_first_name,
                        u.user_middle_name,
                        u.user_last_name
                    ) AS assigned_parson,
                    COUNT(plh.tracking_no) AS tickets,
                    AVG(
                        IFNULL(DATEDIFF(plh.updated_at, plh.created_at), 0)
                    ) AS avg_taken_days
                FROM process_list_hist plh
                LEFT JOIN users u
                    ON plh.updated_by = u.id
                    AND u.user_status = 'active'
                WHERE plh.updated_at BETWEEN :start_date AND :end_date
                  AND plh.process_type = 2
                  AND plh.status_id = 4
                  AND u.employee_id IN ({placeholders})
                GROUP BY u.user_email, assigned_parson
            ) tm
            GROUP BY user_email, assigned_parson
        """)

        async with self._session_factory() as session:
            result = await session.execute(sql, params)
            rows = result.fetchall()

        logger.debug(
            "support_ticket_scores_fetched",
            count=len(rows),
            year=year,
            month=month,
        )
        return [
            {
                "user_email": (row.user_email or "").lower(),
                "assigned_parson": row.assigned_parson or "",
                "total_tickets": int(row.total_tickets or 0),
                "average_taken_days": float(row.average_taken_days or 0),
            }
            for row in rows
        ]
