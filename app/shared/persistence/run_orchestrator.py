"""
app/shared/persistence/run_orchestrator.py
──────────────────────────────────────────
Generic EvaluationRun lifecycle manager — no team-specific logic.

Wraps EvaluationRepository to provide a clean async API for the orchestrator:
    - create(year, month, team) → run row (PENDING)
    - mark_running(run)
    - mark_completed(run, partial=False)
    - mark_failed(run, error)
    - finalise(run, processed, failed) → status side-effect
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.evaluation_run import EvaluationRun, EvaluationStatus
from app.repositories.evaluation_repository import EvaluationRepository


class RunOrchestrator:
    """
    High-level helper for EvaluationRun state transitions.

    Always created per-orchestrator-invocation (i.e. bound to a single
    AsyncSession). Stateless after construction except for the session.
    """

    def __init__(self, db: AsyncSession) -> None:
        self._db = db
        self._repo = EvaluationRepository(db)

    async def create(
        self, year: int, month: int, team: str, triggered_by: str = "api"
    ) -> EvaluationRun:
        """Create a new PENDING run row and commit it. Returns refreshed row."""
        run = await self._repo.create(
            EvaluationRun(
                year=year,
                month=month,
                team=team,
                status=EvaluationStatus.PENDING,
                triggered_by=triggered_by,
            )
        )
        await self._db.commit()
        await self._db.refresh(run)
        return run

    async def mark_running(self, run: EvaluationRun) -> None:
        run.status = EvaluationStatus.RUNNING
        run.started_at = datetime.now(UTC)
        await self._db.flush()

    async def mark_completed(
        self, run: EvaluationRun, partial: bool = False
    ) -> None:
        run.status = EvaluationStatus.PARTIAL if partial else EvaluationStatus.COMPLETED
        run.finished_at = datetime.now(UTC)
        await self._db.flush()

    async def mark_failed(self, run: EvaluationRun, error: str) -> None:
        run.status = EvaluationStatus.FAILED
        run.finished_at = datetime.now(UTC)
        run.error_message = error[:1000]
        await self._db.flush()

    async def finalise(
        self, run: EvaluationRun, processed: int, failed: int
    ) -> None:
        """
        Decide final status from processed/failed counts and persist.

        Rules (preserved from legacy support_evaluations.execute_support_bulk_run):
          - processed == 0         → FAILED
          - failed > 0             → PARTIAL
          - else                   → COMPLETED
        """
        if processed == 0:
            await self.mark_failed(run, error=f"All {failed} employees failed scoring.")
        elif failed > 0:
            await self.mark_completed(run, partial=True)
        else:
            await self.mark_completed(run, partial=False)
        await self._db.commit()
