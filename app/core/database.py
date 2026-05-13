"""
app/core/database.py
────────────────────
Async SQLAlchemy 2.0 engine and session factory.

Design decisions:
- Uses async_sessionmaker so every request gets its own session.
- The engine is initialised once at startup via lifespan().
- SQLite WAL mode is activated for concurrent reads.
- Switching to PostgreSQL only requires changing DATABASE_URL in .env.
"""

from collections.abc import AsyncGenerator
from pathlib import Path

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy import event, text

from app.core.config import get_settings

settings = get_settings()


class Base(DeclarativeBase):
    """Shared declarative base for all ORM models."""

    pass


def _build_engine():
    """
    Build the async engine with sane defaults.
    - echo=settings.database_echo for optional SQL logging.
    - connect_args only for SQLite (WAL + foreign keys).
    """
    connect_args: dict = {}
    engine_kwargs: dict = {
        "url": settings.database_url,
        "echo": settings.database_echo,
        "future": True,
    }

    if settings.database_url.startswith("sqlite"):
        # Ensure the data directory exists for SQLite
        db_path_str = settings.database_url.replace("sqlite+aiosqlite:///", "")
        Path(db_path_str).parent.mkdir(parents=True, exist_ok=True)
        connect_args = {"check_same_thread": False}
        engine_kwargs["connect_args"] = connect_args
    else:
        # PostgreSQL: use a reasonable pool for production
        engine_kwargs["pool_size"] = 10
        engine_kwargs["max_overflow"] = 20

    return create_async_engine(**engine_kwargs)


engine = _build_engine()

AsyncSessionFactory: async_sessionmaker[AsyncSession] = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
    autocommit=False,
)


async def init_db() -> None:
    """
    Create all tables and apply SQLite-specific PRAGMAs.
    Called during application startup via lifespan.
    """
    async with engine.begin() as conn:
        if settings.database_url.startswith("sqlite"):
            # Enable WAL mode for better concurrent read performance
            await conn.execute(text("PRAGMA journal_mode=WAL"))
            # Enforce foreign-key constraints at runtime
            await conn.execute(text("PRAGMA foreign_keys=ON"))

        # Import all models so their metadata is registered before create_all
        from app.models import employee, evaluation_run, scores  # noqa: F401

        await conn.run_sync(Base.metadata.create_all)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    FastAPI dependency that yields a database session per request.
    The session is always closed – even if an exception is raised.
    """
    async with AsyncSessionFactory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
