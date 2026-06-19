"""
app/core/mysql_db.py
────────────────────
Dedicated async SQLAlchemy engine and session factory for the remote
MySQL evaluation-results database (employee_performance_summary).

This module is deliberately independent from app/core/database.py so
the SQLite primary database and the MySQL summary database can use
different connection pools, dialects, and lifecycle rules.
"""

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import get_settings
from app.core.database import Base

settings = get_settings()

mysql_engine = create_async_engine(
    settings.mysql_summary_url,
    echo=False,
    pool_size=5,
    max_overflow=10,
)

MySQLSessionFactory: async_sessionmaker[AsyncSession] = async_sessionmaker(
    bind=mysql_engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
    autocommit=False,
)


async def init_mysql_tables() -> None:
    """Create MySQL-specific tables if they don't already exist.

    Only tables that belong in the remote MySQL summary database are
    created here — the primary SQLite tables are handled by init_db().
    """
    from app.models.performance_summary import EmployeePerformanceSummary

    async with mysql_engine.begin() as conn:
        await conn.run_sync(
            Base.metadata.create_all,
            tables=[EmployeePerformanceSummary.__table__],
        )
