"""
app/repositories/evaluation_repository.py
───────────────────────────────────────────
Async data-access layer for EvaluationRun records.
"""

from datetime import datetime, timezone

from sqlalchemy import select

from app.models.evaluation_run import EvaluationRun, EvaluationStatus
from app.repositories.base import BaseRepository


class EvaluationRepository(BaseRepository[EvaluationRun]):
    model = EvaluationRun

    async def get_by_team_and_period(
        self, team: str, year: int, month: int
    ) -> EvaluationRun | None:
        result = await self._session.execute(
            select(EvaluationRun).where(
                EvaluationRun.team == team,
                EvaluationRun.year == year,
                EvaluationRun.month == month,
            )
        )
        return result.scalar_one_or_none()

    async def get_latest_for_team(
        self, team: str, limit: int = 5
    ) -> list[EvaluationRun]:
        result = await self._session.execute(
            select(EvaluationRun)
            .where(EvaluationRun.team == team)
            .order_by(EvaluationRun.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def mark_running(self, run: EvaluationRun) -> EvaluationRun:
        run.status = EvaluationStatus.RUNNING
        run.started_at = datetime.now(timezone.utc)
        await self._session.flush()
        return run

    async def mark_completed(
        self, run: EvaluationRun, partial: bool = False
    ) -> EvaluationRun:
        run.status = EvaluationStatus.PARTIAL if partial else EvaluationStatus.COMPLETED
        run.finished_at = datetime.now(timezone.utc)
        await self._session.flush()
        return run

    async def mark_failed(self, run: EvaluationRun, error: str) -> EvaluationRun:
        run.status = EvaluationStatus.FAILED
        run.finished_at = datetime.now(timezone.utc)
        run.error_message = error[:1000]
        await self._session.flush()
        return run
