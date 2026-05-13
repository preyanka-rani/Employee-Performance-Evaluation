"""
app/workers/code_analysis.py
──────────────────────────────
Celery task: run the LangGraph MR analysis workflow for a single employee.

This task is used when we want to parallelise MR analysis across workers.
The monthly_evaluation task can dispatch these individually and gather results.
"""

import asyncio
from typing import Any

from app.workers.celery_app import celery_app
from app.core.logging_config import get_logger

logger = get_logger(__name__)


def _run_async(coro: Any) -> Any:
    return asyncio.get_event_loop().run_until_complete(coro)


@celery_app.task(
    name="app.workers.code_analysis.analyze_mr_task",
    bind=True,
    max_retries=2,
    default_retry_delay=30,
)
def analyze_mr_task(
    self,
    employee_email: str,
    gitlab_username: str,
    evaluation_run_id: int,
    year: int,
    month: int,
) -> dict:
    """
    Analyse MRs for a single employee and return the aggregate quality score.

    This is a standalone Celery task for use when horizontal scaling of
    AI analysis is needed (one task per employee, all in parallel).

    Returns:
        {
          "employee_email": str,
          "aggregate_score": float,
          "mr_count": int,
          "error": None | str,
        }
    """
    logger.info(
        "analyze_mr_task_start",
        email=employee_email,
        year=year,
        month=month,
    )

    try:
        result = _run_async(
            _async_analyze_mr(
                employee_email=employee_email,
                gitlab_username=gitlab_username,
                evaluation_run_id=evaluation_run_id,
                year=year,
                month=month,
            )
        )
        return result
    except Exception as exc:
        logger.error("analyze_mr_task_failed", email=employee_email, error=str(exc))
        try:
            raise self.retry(exc=exc)
        except self.MaxRetriesExceededError:
            return {
                "employee_email": employee_email,
                "aggregate_score": 70.0,
                "mr_count": 0,
                "error": str(exc),
            }


async def _async_analyze_mr(
    employee_email: str,
    gitlab_username: str,
    evaluation_run_id: int,
    year: int,
    month: int,
) -> dict:
    from app.services.workflows.mr_analysis import run_mr_analysis

    state = await run_mr_analysis(
        employee_email=employee_email,
        gitlab_username=gitlab_username,
        evaluation_run_id=evaluation_run_id,
        year=year,
        month=month,
    )

    return {
        "employee_email": employee_email,
        "aggregate_score": state["aggregate_score"],
        "mr_count": len(state["mr_scores"]),
        "mr_scores": state["mr_scores"],
        "error": state.get("error"),
    }
