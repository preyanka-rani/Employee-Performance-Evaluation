"""
app/services/support_teams/data_sources/crm_client.py
──────────────────────────────────────────────────────
Read-only MySQL client for support team CRM activity log data.

Source table: project_activity_log (MySQL CRM DB)
Mirrors: funcational_log_activities.py / perform_crm.sql from documentation.

All SQL queries are parameterised — no string interpolation of user data.
Only SELECT statements are issued; no DML operations are performed.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.core.logging_config import get_logger

settings = get_settings()
logger = get_logger(__name__)


class SupportCRMClient:
    """
    Fetches CRM activity log data for support team employees.

    Methods:
      get_crm_log_hours()    — aggregated log hours per employee for a month
      get_crm_descriptions() — individual log entries for sentiment analysis
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

    async def get_crm_log_hours(
        self,
        employee_ids: list[str],
        year: int,
        month: int,
    ) -> list[dict]:
        """
        Return aggregated CRM log hours per employee for the given month.

        Mirrors perform_crm.sql from documentation Section 2.1.

        Returns list of dicts:
          {employee_id, user_email, year, month_id, total_hours}
        """
        if not employee_ids:
            return []

        placeholders = ", ".join(f":emp_{i}" for i in range(len(employee_ids)))
        params: dict = {f"emp_{i}": eid for i, eid in enumerate(employee_ids)}
        params["year"] = year
        params["month"] = month
        params["time_suffix"] = ":00"

        # Both source tables are unioned — mirrors perform_crm.sql UNION ALL
        sql = text(f"""
            SELECT
                employee_id,
                user_email,
                year,
                month_id,
                ROUND(SUM(log_hour), 2) AS total_hours
            FROM (
                SELECT
                    u.employee_id,
                    u.user_email,
                    YEAR(alog.work_dt)  AS year,
                    MONTH(alog.work_dt) AS month_id,
                    ROUND(
                        IFNULL(TIME_TO_SEC(CONCAT(alog.work_duation, :time_suffix)) / 3600, 0),
                        2
                    ) AS log_hour
                FROM project_activity_log alog
                LEFT JOIN users u ON alog.created_by = u.id
                LEFT JOIN (SELECT * FROM team_members WHERE status = 1) tm ON u.id = tm.user_id
                LEFT JOIN team_info team ON tm.team_id = team.id
                WHERE u.user_status = 'active'
                  AND u.employee_id IN ({placeholders})
                  AND YEAR(alog.work_dt)  = :year
                  AND MONTH(alog.work_dt) = :month

                UNION ALL

                SELECT
                    u.employee_id,
                    u.user_email,
                    YEAR(clab.work_dt)  AS year,
                    MONTH(clab.work_dt) AS month_id,
                    IFNULL(clab.hour_spent, 0) AS log_hour
                FROM project_activity_log_clab clab
                LEFT JOIN users u ON clab.user_email = u.user_email
                LEFT JOIN (SELECT * FROM team_members WHERE status = 1) tm ON u.id = tm.user_id
                LEFT JOIN team_info team ON tm.team_id = team.id
                WHERE u.user_status = 'active'
                  AND u.employee_id IN ({placeholders})
                  AND YEAR(clab.work_dt)  = :year
                  AND MONTH(clab.work_dt) = :month
            ) combined
            GROUP BY employee_id, user_email, year, month_id
        """)

        async with self._session_factory() as session:
            result = await session.execute(sql, params)
            rows = result.fetchall()

        logger.debug(
            "support_crm_log_hours_fetched",
            count=len(rows),
            year=year,
            month=month,
        )
        return [
            {
                "employee_id": row.employee_id,
                "user_email": (row.user_email or "").lower(),
                "year": row.year,
                "month_id": row.month_id,
                "total_hours": float(row.total_hours or 0),
            }
            for row in rows
        ]

    async def get_crm_descriptions(
        self,
        employee_ids: list[str],
        year: int,
        month: int,
    ) -> list[dict]:
        """
        Return individual CRM log descriptions for sentiment analysis.

        Only returns rows with a non-null description.

        Returns list of dicts:
          {employee_id, user_email, description, log_hour}
        """
        if not employee_ids:
            return []

        placeholders = ", ".join(f":emp_{i}" for i in range(len(employee_ids)))
        params: dict = {f"emp_{i}": eid for i, eid in enumerate(employee_ids)}
        params["year"] = year
        params["month"] = month
        params["time_suffix"] = ":00"

        # Both source tables are unioned — mirrors perform_crm.sql UNION ALL
        sql = text(f"""
            SELECT employee_id, user_email, description, log_hour
            FROM (
                SELECT
                    u.employee_id,
                    u.user_email,
                    alog.description,
                    ROUND(
                        IFNULL(TIME_TO_SEC(CONCAT(alog.work_duation, :time_suffix)) / 3600, 0),
                        2
                    ) AS log_hour
                FROM project_activity_log alog
                LEFT JOIN users u ON alog.created_by = u.id
                WHERE u.user_status = 'active'
                  AND u.employee_id IN ({placeholders})
                  AND YEAR(alog.work_dt)  = :year
                  AND MONTH(alog.work_dt) = :month
                  AND alog.description IS NOT NULL
                  AND alog.description <> ''

                UNION ALL

                SELECT
                    u.employee_id,
                    u.user_email,
                    clab.issue_details AS description,
                    IFNULL(clab.hour_spent, 0) AS log_hour
                FROM project_activity_log_clab clab
                LEFT JOIN users u ON clab.user_email = u.user_email
                WHERE u.user_status = 'active'
                  AND u.employee_id IN ({placeholders})
                  AND YEAR(clab.work_dt)  = :year
                  AND MONTH(clab.work_dt) = :month
                  AND clab.issue_details IS NOT NULL
                  AND clab.issue_details <> ''
            ) combined
        """)

        async with self._session_factory() as session:
            result = await session.execute(sql, params)
            rows = result.fetchall()

        logger.debug(
            "support_crm_descriptions_fetched",
            count=len(rows),
            year=year,
            month=month,
        )
        return [
            {
                "employee_id": row.employee_id,
                "user_email": (row.user_email or "").lower(),
                "description": row.description or "",
                "log_hour": float(row.log_hour or 0),
            }
            for row in rows
        ]

    async def get_employee_ids_by_emails(
        self,
        emails: list[str],
    ) -> dict[str, str]:
        """
        Resolve email → employee_id mapping from CRM users table.

        Returns {email: employee_id} for all emails found.
        """
        if not emails:
            return {}

        lowered = [e.lower() for e in emails]
        placeholders = ", ".join(f":email_{i}" for i in range(len(lowered)))
        params: dict = {f"email_{i}": email for i, email in enumerate(lowered)}

        sql = text(f"""
            SELECT employee_id, LOWER(user_email) AS user_email
            FROM users
            WHERE user_status = 'active'
              AND LOWER(user_email) IN ({placeholders})
        """)

        async with self._session_factory() as session:
            result = await session.execute(sql, params)
            rows = result.fetchall()

        return {row.user_email: str(row.employee_id) for row in rows}
