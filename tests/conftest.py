"""
tests/conftest.py
──────────────────
Shared pytest fixtures:
  - In-memory SQLite async engine (no disk I/O in tests)
  - Async test client via httpx + ASGITransport
  - Pre-seeded test employee fixture
"""

import asyncio
from typing import AsyncGenerator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.database import Base
from app.main import app
from app.api.deps import get_db
from app.core.security import create_access_token
from app.models.employee import Employee
from app.models.evaluation_run import EvaluationRun, EvaluationStatus

TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"


@pytest.fixture(scope="session")
def event_loop():
    """Use a single event loop for the entire test session."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture(scope="session")
async def engine():
    eng = create_async_engine(TEST_DATABASE_URL, echo=False)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture
async def db_session(engine) -> AsyncGenerator[AsyncSession, None]:
    """Fresh session per test; rolls back on teardown."""
    TestSessionFactory = async_sessionmaker(engine, expire_on_commit=False)
    async with TestSessionFactory() as session:
        yield session
        await session.rollback()


@pytest_asyncio.fixture
async def client(engine) -> AsyncGenerator[AsyncClient, None]:
    """AsyncClient with the in-memory DB injected."""
    TestSessionFactory = async_sessionmaker(engine, expire_on_commit=False)

    async def override_get_db():
        async with TestSessionFactory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db

    token = create_access_token({"sub": "test_user"})
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
    ) as ac:
        yield ac

    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def test_employee(db_session: AsyncSession) -> Employee:
    """A pre-seeded active developer employee."""
    emp = Employee(
        employee_id="DEV001",
        name="Alice Developer",
        email="alice@example.com",
        team="developer",
        gitlab_username="alice_dev",
        is_active=True,
    )
    db_session.add(emp)
    await db_session.commit()
    await db_session.refresh(emp)
    return emp


@pytest_asyncio.fixture
async def test_evaluation_run(db_session: AsyncSession) -> EvaluationRun:
    """A pre-seeded pending evaluation run."""
    run = EvaluationRun(
        year=2024,
        month=12,
        team="developer",
        status=EvaluationStatus.PENDING,
        triggered_by="test",
    )
    db_session.add(run)
    await db_session.commit()
    await db_session.refresh(run)
    return run
