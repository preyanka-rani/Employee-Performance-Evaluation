"""
alembic/env.py
───────────────
Async-compatible Alembic environment for SQLAlchemy 2.0.

Supports:
  - Online mode (run_async_migrations)
  - Offline mode (generate SQL without connecting)

DATABASE_URL is read from the application settings so migrations
always target the correct database (SQLite in dev, PostgreSQL in prod).
"""

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

# Import all models so Alembic can detect schema changes via autogenerate
from app.core.database import Base  # noqa: F401 — registers metadata
from app.models.employee import Employee  # noqa: F401
from app.models.evaluation_run import EvaluationRun  # noqa: F401
from app.models.scores import (  # noqa: F401
    AttendanceScore,
    CodeQualityScore,
    FinalScore,
    SentimentScore,
    TLAssessmentScore,
    WorkLogScore,
)

# Alembic Config object (gives access to values in alembic.ini)
config = context.config

# Interpret the config file for Python logging
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Use SQLAlchemy metadata for autogenerate support
target_metadata = Base.metadata


def get_url() -> str:
    """Read DATABASE_URL from application settings at migration time."""
    from app.core.config import get_settings

    return get_settings().database_url


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (no DB connection needed)."""
    url = get_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Run migrations in 'online' mode with an async engine."""
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = get_url()

    connectable = async_engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
