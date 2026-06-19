"""
app/api/v1/evaluations.py
──────────────────────────
Endpoints for triggering and monitoring evaluation runs.

POST /api/v1/evaluations/run          – Enqueue a monthly evaluation via Celery
POST /api/v1/evaluations/bulk-run     – Synchronous bulk evaluation from Excel upload
GET  /api/v1/evaluations/{run_id}     – Fetch evaluation run details
GET  /api/v1/evaluations/status/{run_id} – Lightweight status poll
"""

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.core.logging_config import get_logger
from app.orchestrator import run_supervisor
from app.repositories.evaluation_repository import EvaluationRepository
from app.schemas.evaluation import (
    EvaluationRunRequest,
    EvaluationRunResponse,
    EvaluationStatusResponse,
)
from app.shared.excel_parser.parser import ExcelParseError

_log = get_logger(__name__)

router = APIRouter(prefix="/evaluations", tags=["evaluations"])


@router.post(
    "/run",
    response_model=EvaluationRunResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def trigger_evaluation(
    payload: EvaluationRunRequest,
    db: AsyncSession = Depends(get_db),
) -> EvaluationRunResponse:
    """
    Enqueue a monthly Developer evaluation run.

    Delegates to Celery; returns immediately with the run record.
    """
    from app.models.evaluation_run import EvaluationRun, EvaluationStatus
    from app.workers.monthly_evaluation import run_monthly_evaluation_task

    # Prevent duplicate runs for the same period
    eval_repo = EvaluationRepository(db)
    existing = await eval_repo.get_by_team_and_period(
        team=payload.team,
        year=payload.year,
        month=payload.month,
    )
    if existing and existing.status not in ("failed", "partial"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Evaluation for {payload.team}/{payload.year}-{payload.month} already exists.",
        )

    run = await eval_repo.create(
        EvaluationRun(
            year=payload.year,
            month=payload.month,
            team=payload.team,
            status=EvaluationStatus.PENDING,
            triggered_by="api",
        )
    )
    await db.commit()

    # Dispatch Celery task (async, non-blocking)
    run_monthly_evaluation_task.delay(
        run_id=run.id,
        year=payload.year,
        month=payload.month,
        team=payload.team,
    )

    return EvaluationRunResponse.model_validate(run)


@router.get("/{run_id}", response_model=EvaluationRunResponse)
async def get_evaluation(
    run_id: int,
    db: AsyncSession = Depends(get_db),
) -> EvaluationRunResponse:
    eval_repo = EvaluationRepository(db)
    run = await eval_repo.get_by_id(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Evaluation run not found.")
    return EvaluationRunResponse.model_validate(run)


@router.get("/status/{run_id}", response_model=EvaluationStatusResponse)
async def get_evaluation_status(
    run_id: int,
    db: AsyncSession = Depends(get_db),
) -> EvaluationStatusResponse:
    eval_repo = EvaluationRepository(db)
    run = await eval_repo.get_by_id(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Evaluation run not found.")
    return EvaluationStatusResponse(
        run_id=run.id,
        status=run.status,
        team=run.team,
        year=run.year,
        month=run.month,
        error_message=run.error_message,
    )


@router.post("/bulk-run", status_code=status.HTTP_200_OK)
async def bulk_run_evaluation(
    team: str = Form(
        ...,
        description=(
            "Team name — any human-readable form accepted for any team. "
            "Examples: 'developer', 'Tech Support', 'Implementation & ITS', "
            "'tech_support', 'impl_its', 'onsite_support', 'production','cirt_infra','sqa', 'hajj_helpdesk', 'supply_chain','finance','hr'."
        ),
    ),
    month: int = Form(..., ge=1, le=12, description="Evaluation month (1-12)"),
    year: int = Form(..., ge=2020, description="Evaluation year"),
    file: UploadFile = File(
        ..., description="Excel (.xlsx) with TL marks per employee"
    ),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Synchronous bulk evaluation endpoint.

    Workflow:
        1. Parse the uploaded Excel file → extract TL marks per employee
        2. Create an EvaluationRun record (status=RUNNING)
        3. Upsert employees and their TLAssessmentScore rows
        4. Run DeveloperScorer (or team-specific scorer) per employee
        5. Mark the run as COMPLETED (or PARTIAL on partial failure)
        6. Generate a formatted Excel report saved to outputs/reports/
        7. Return summary stats + report path
    """

    # ── 0. Resolve team key and short-circuit on conflicts ───────────────────
    from app.shared.registry import TeamRegistry

    try:
        team_key = TeamRegistry.resolve(team)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc

    log = _log.bind(team=team_key, year=year, month=month)
    log.info("bulk_run_start", filename=file.filename, size=file.size)

    # ── 1. Delegate everything to the supervisor graph ──────────────────────
    content = await file.read()
    try:
        summary = await run_supervisor(
            file_bytes=content,
            raw_team_input=team,
            year=year,
            month=month,
            db=db,
        )
    except ValueError as exc:
        # Team key not found / parse fatal — surface as 400
        log.error("bulk_run_invalid_request", error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    except ExcelParseError as exc:
        log.error("bulk_run_parse_error", errors=exc.errors)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"message": "Excel parsing failed", "errors": exc.errors},
        ) from exc

    if not summary.get("processed_count") and summary.get("status") == "failed":
        # All rows failed — return 422 so the client knows
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=summary,
        )

    return summary
