"""
app/shared/data_sources/mysql_client.py
───────────────────────────────────────
Read-only MySQL client for fetching source data (attendance, work logs).

Design decisions:
- Uses SQLAlchemy async engine with aiomysql driver.
- All connections are READ-ONLY — no INSERT/UPDATE/DELETE ever issued.
- Two separate engines: CRM database and HR/Attendance database.
- Connection pooling with sane limits to avoid overwhelming source DBs.
- All SQL queries are parameterised to prevent injection.

NOTE: This file is a verbatim move from app/services/data_sources/mysql_client.py.
      No functional change. No team-specific logic — generic MySQL access only.
"""

from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.core.logging_config import get_logger

settings = get_settings()
logger = get_logger(__name__)


@dataclass
class AttendanceRecord:
    user_email: str
    year: int
    month_id: int
    work_days: int
    actual_work_days: int
    present: int
    late_attendance: int
    late_days: int
    attendance_score: float


@dataclass
class WorkLogRecord:
    user_email: str
    year: int
    month_id: int
    log_hour: float
    description: str | None = None


class MySQLCRMClient:
    """
    Read-only client for the CRM / work-log MySQL database.

    Fetches:
      - Developer work log hours (per employee per month)
      - Log descriptions for sentiment analysis
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

    async def get_developer_work_logs(
        self,
        employee_ids: list[str],
        year: int,
        month: int,
    ) -> list[WorkLogRecord]:
        """
        Fetch aggregated log hours for developers.

        Unions BOTH source tables (mirrors perform_crm.sql):
          1. project_activity_log     — CRM activity logs (joined via created_by)
          2. project_activity_log_clab — Codelab logs (joined via user_email)
        """
        if not employee_ids:
            return []

        placeholders = ", ".join(f":emp_{i}" for i in range(len(employee_ids)))
        params: dict = {f"emp_{i}": eid for i, eid in enumerate(employee_ids)}
        params["year"] = year
        params["month"] = month
        params["time_suffix"] = ":00"

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
                    ROUND(IFNULL(TIME_TO_SEC(CONCAT(alog.work_duation, :time_suffix)) / 3600, 0), 2)
                        AS log_hour
                FROM project_activity_log alog
                JOIN users u ON alog.created_by = u.id
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
                JOIN users u ON clab.user_email = u.user_email
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

        return [
            {
                "employee_id": row.employee_id,
                "user_email": row.user_email,
                "year": row.year,
                "month_id": row.month_id,
                "total_hours": float(row.total_hours or 0),
            }
            for row in rows
        ]

    async def get_developer_log_descriptions(
        self,
        employee_ids: list[str],
        year: int,
        month: int,
    ) -> list[WorkLogRecord]:
        """
        Fetch individual log descriptions for sentiment analysis.

        Unions BOTH source tables (mirrors perform_crm.sql):
          1. project_activity_log.description
          2. project_activity_log_clab.issue_details
        """
        if not employee_ids:
            return []

        placeholders = ", ".join(f":emp_{i}" for i in range(len(employee_ids)))
        params: dict = {f"emp_{i}": eid for i, eid in enumerate(employee_ids)}
        params["year"] = year
        params["month"] = month
        params["time_suffix"] = ":00"

        sql = text(f"""
            SELECT
                employee_id,
                user_email,
                year,
                month_id,
                description,
                log_hour
            FROM (
                SELECT
                    u.employee_id,
                    u.user_email,
                    YEAR(alog.work_dt)  AS year,
                    MONTH(alog.work_dt) AS month_id,
                    alog.description    AS description,
                    ROUND(IFNULL(TIME_TO_SEC(CONCAT(alog.work_duation, :time_suffix)) / 3600, 0), 2)
                        AS log_hour
                FROM project_activity_log alog
                JOIN users u ON alog.created_by = u.id
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
                    clab.issue_details  AS description,
                    IFNULL(clab.hour_spent, 0) AS log_hour
                FROM project_activity_log_clab clab
                JOIN users u ON clab.user_email = u.user_email
                WHERE u.user_status = 'active'
                  AND u.employee_id IN ({placeholders})
                  AND YEAR(clab.work_dt)  = :year
                  AND MONTH(clab.work_dt) = :month
            ) combined
        """)

        async with self._session_factory() as session:
            result = await session.execute(sql, params)
            rows = result.fetchall()

        return [
            {
                "employee_id": row.employee_id,
                "user_email": row.user_email,
                "year": row.year,
                "month_id": row.month_id,
                "description": row.description,
                "log_hour": float(row.log_hour or 0),
            }
            for row in rows
        ]

    async def get_employee_ids_by_emails(
        self,
        emails: list[str],
    ) -> dict[str, str]:
        """
        Look up employee_id for each email from the CRM users table.
        Returns {email: employee_id} for all emails that were found.
        """
        if not emails:
            return {}

        placeholders = ", ".join(f":email_{i}" for i in range(len(emails)))
        params: dict = {f"email_{i}": email.lower() for i, email in enumerate(emails)}

        sql = text(f"""
            SELECT employee_id, LOWER(user_email) AS user_email
            FROM users
            WHERE user_status = 'active'
              AND LOWER(user_email) IN ({placeholders})
        """)

        async with self._session_factory() as session:
            result = await session.execute(sql, params)
            rows = result.fetchall()

        return {row.user_email: row.employee_id for row in rows if row.employee_id}

    async def close(self) -> None:
        await self._engine.dispose()


class MySQLHRClient:
    """
    Read-only client for the HR / Attendance MySQL database.

    Fetches:
      - Monthly attendance summary (present days, late days, work days)
    """

    def __init__(self) -> None:
        self._engine = create_async_engine(
            settings.mysql_hr_dsn,
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

    async def get_attendance(
        self,
        employee_ids: list[str],
        year: int,
        month: int,
    ) -> list[AttendanceRecord]:
        """
        Fetch attendance scores for the given employees and period.
        Reference: base_attendence.sql (§1.1 Office Discipline)

        Formula: LEAST(ROUND(((present - FLOOR(late/3)) / actual_work_days) * 100, 2), 100)
        """
        if not employee_ids:
            return []

        placeholders = ", ".join(f":emp_{i}" for i in range(len(employee_ids)))
        params: dict = {f"emp_{i}": eid for i, eid in enumerate(employee_ids)}
        params["year"] = year
        params["month"] = month

        sql = text(f"""
            SELECT
                u.employee_id,
                u.user_email,
                YEAR(ma.month)   AS year_at,
                MONTH(ma.month)  AS month_id_at,
                ma.work_days,
                (ma.work_days - ma.leave - ma.day_off) AS actual_work_days,
                ma.present,
                ma.late_attendance,
                FLOOR(ma.late_attendance / 3) AS late_days,
                LEAST(
                    ROUND(
                        ((ma.present - FLOOR(ma.late_attendance / 3))
                            / NULLIF(ma.work_days - ma.leave - ma.day_off, 0)
                        ) * 100,
                    2),
                100) AS attendance_score
            FROM monthly_attendance_summary ma
            JOIN users u ON ma.employee_id = u.employee_id
            WHERE u.user_status = 'active'
              AND u.employee_id IN ({placeholders})
              AND YEAR(ma.month)  = :year
              AND MONTH(ma.month) = :month
        """)

        async with self._session_factory() as session:
            result = await session.execute(sql, params)
            rows = result.fetchall()

        return [
            {
                "employee_id": row.employee_id,
                "user_email": row.user_email,
                "year": row.year_at,
                "month_id": row.month_id_at,
                "work_days": int(row.work_days or 0),
                "actual_work_days": int(row.actual_work_days or 0),
                "present": int(row.present or 0),
                "late": int(row.late_attendance or 0),
                "late_days": int(row.late_days or 0),
                "attendance_score": float(row.attendance_score or 0),
            }
            for row in rows
        ]

    async def close(self) -> None:
        await self._engine.dispose()
