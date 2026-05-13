"""
app/workers/celery_app.py
──────────────────────────
Celery application instance with Redis broker/backend.

Beat schedule: triggers monthly evaluation on the 1st of each month at 02:00.
"""

from celery import Celery
from celery.schedules import crontab

from app.core.config import get_settings

settings = get_settings()

celery_app = Celery(
    "employee_eval",
    broker=settings.celery_broker_url,
    backend=settings.redis_url,
    include=[
        "app.workers.monthly_evaluation",
        "app.workers.code_analysis",
    ],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    worker_prefetch_multiplier=1,
    task_acks_late=True,  # ack only after successful completion
    task_reject_on_worker_lost=True,  # re-queue if worker dies mid-task
    beat_schedule={
        "monthly-evaluation-trigger": {
            "task": "app.workers.monthly_evaluation.run_monthly_evaluation_task",
            "schedule": crontab(day_of_month="1", hour="2", minute="0"),
            "kwargs": {
                "team": "developer",
                # year/month resolved inside the task at runtime
            },
        }
    },
)
