"""
app/workers/monthly_evaluation.py
───────────────────────────────────
Celery task: orchestrate the full Developer evaluation pipeline for a period.

Flow per evaluation run:
  1. Resolve year/month (defaults to previous calendar month)
  2. Load all active employees for the team
  3. For each employee → run DeveloperScorer.calculate()
  4. Update EvaluationRun status to completed / partial / failed
"""

import asyncio
import datetime
from typing import Any

from celery import Task

from app.workers.celery_app import celery_app
from app.core.logging_config import get_logger

logger = get_logger(__name__)


def _previous_month() -> tuple[int, int]:
    """Return (year, month) for the previous calendar month."""
    today = datetime.date.today()
    first = today.replace(day=1)
    last_month = first - datetime.timedelta(days=1)
    return last_month.year, last_month.month


def _run_async(coro: Any) -> Any:
    """Run an async coroutine from a sync Celery task context."""
    return asyncio.get_event_loop().run_until_complete(coro)


@celery_app.task(
    name="app.workers.monthly_evaluation.run_monthly_evaluation_task",
    bind=True,
    max_retries=2,
    default_retry_delay=60,
)
def run_monthly_evaluation_task(
    self: Task,
    run_id: int | None = None,
    year: int | None = None,
    month: int | None = None,
    team: str = "developer",
) -> dict:
    """
    Run the full monthly evaluation for all employees in `team`.

    If year/month are not provided, defaults to the previous calendar month.
    If run_id is None, a new EvaluationRun record is created.

    Returns a summary dict with success/failure counts.
    """
    if year is None or month is None:
        year, month = _previous_month()

    logger.info(
        "monthly_eval_task_start",
        team=team,
        year=year,
        month=month,
        run_id=run_id,
    )

    try:
        result = _run_async(
            _async_monthly_evaluation(run_id=run_id, year=year, month=month, team=team)
        )
        return result
    except Exception as exc:
        logger.error("monthly_eval_task_failed", error=str(exc))
        try:
            raise self.retry(exc=exc)
        except self.MaxRetriesExceededError:
            return {"status": "failed", "error": str(exc)}


async def _async_monthly_evaluation(
    run_id: int | None,
    year: int,
    month: int,
    team: str,
) -> dict:
    """Async implementation of the monthly evaluation pipeline."""
    from app.core.database import AsyncSessionFactory
    from app.models.evaluation_run import EvaluationRun, EvaluationStatus
    from app.repositories.employee_repository import EmployeeRepository
    from app.repositories.evaluation_repository import EvaluationRepository
    from app.services.scoring.developer import DeveloperScorer

    async with AsyncSessionFactory() as db:
        eval_repo = EvaluationRepository(db)
        emp_repo = EmployeeRepository(db)

        # Get or create the EvaluationRun record
        if run_id is not None:
            run = await eval_repo.get_by_id(run_id)
        else:
            run = await eval_repo.create(
                EvaluationRun(
                    year=year,
                    month=month,
                    team=team,
                    status=EvaluationStatus.PENDING,
                    triggered_by="celery_beat",
                )
            )
            await db.commit()
            await db.refresh(run)

        if run is None:
            raise ValueError(f"EvaluationRun {run_id} not found.")

        await eval_repo.mark_running(run)
        await db.commit()

        employees = await emp_repo.get_by_team(team)
        active_employees = [e for e in employees if e.is_active]

        scorer = DeveloperScorer()
        success_count = 0
        failure_count = 0

        for employee in active_employees:
            try:
                await scorer.calculate(
                    employee=employee,
                    evaluation_run_id=run.id,
                    year=year,
                    month=month,
                    db=db,
                )
                await db.commit()
                success_count += 1
            except Exception as exc:
                logger.error(
                    "employee_scoring_failed",
                    employee=employee.employee_id,
                    error=str(exc),
                )
                await db.rollback()
                failure_count += 1

        # Determine final run status
        if failure_count == 0:
            await eval_repo.mark_completed(run)
        elif success_count > 0:
            await eval_repo.mark_completed(run, partial=True)
        else:
            await eval_repo.mark_failed(run, error="All employees failed to evaluate.")

        await db.commit()

        summary = {
            "run_id": run.id,
            "team": team,
            "year": year,
            "month": month,
            "total": len(active_employees),
            "success": success_count,
            "failed": failure_count,
            "status": "completed" if failure_count == 0 else "partial",
        }
        logger.info("monthly_eval_task_done", **summary)
        return summary
