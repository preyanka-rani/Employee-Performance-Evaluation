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

_log = get_logger(__name__)
from app.repositories.evaluation_repository import EvaluationRepository
from app.schemas.evaluation import (
    EvaluationRunRequest,
    EvaluationRunResponse,
    EvaluationStatusResponse,
)

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
    team: str = Form(..., description="Team name, e.g. 'developer'"),
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
    from app.models.employee import Employee
    from app.models.evaluation_run import EvaluationRun, EvaluationStatus
    from app.models.scores import TLAssessmentScore
    from app.repositories.employee_repository import EmployeeRepository
    from app.repositories.score_repository import TLAssessmentRepository
    from app.services.data_sources.excel_parser import ExcelParseError, parse_tl_excel
    from app.services.reporting.report_generator import (
        generate_code_quality_report,
        generate_excel_report,
    )
    from app.services.scoring.base import get_scorer

    log = _log.bind(team=team, year=year, month=month)

    # ── 1. Parse Excel ────────────────────────────────────────────────────────
    log.info("bulk_run_start", filename=file.filename, size=file.size)
    try:
        content = await file.read()
        tl_rows = parse_tl_excel(content)
        log.info("excel_parsed_ok", row_count=len(tl_rows))

        # ── 1b. Resolve missing employee_ids from MySQL ───────────────────────
        missing_id_emails = [r.email for r in tl_rows if not r.employee_id]
        if missing_id_emails:
            from app.services.data_sources.mysql_client import MySQLCRMClient

            crm = MySQLCRMClient()
            try:
                resolved = await crm.get_employee_ids_by_emails(missing_id_emails)
            finally:
                await crm.close()

            still_missing: list[str] = []
            for r in tl_rows:
                if not r.employee_id:
                    found_id = resolved.get(r.email)
                    if found_id:
                        r.employee_id = found_id
                        log.info(
                            "employee_id_resolved_from_mysql",
                            email=r.email,
                            employee_id=found_id,
                        )
                    else:
                        still_missing.append(r.email)
                        log.warning(
                            "employee_id_not_found_in_mysql",
                            email=r.email,
                        )

            # Drop rows that still have no employee_id
            tl_rows = [r for r in tl_rows if r.employee_id]
            if still_missing:
                log.error(
                    "employee_id_lookup_failed",
                    emails=still_missing,
                    message="Rows dropped — employee_id not found in MySQL users table",
                )

        if not tl_rows:
            log.warning("excel_no_valid_rows_after_id_lookup")
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="No valid employee rows after employee_id resolution.",
            )

        for r in tl_rows:
            log.debug(
                "tl_row",
                employee_id=r.employee_id,
                email=r.email,
                name=r.name,
                tl_ps=r.tl_problem_solving,
                tl_kpi=r.tl_kpi,
                tl_general=r.tl_general,
                tl_total=r.tl_total,
            )
    except ExcelParseError as exc:
        log.error("excel_parse_error", errors=exc.errors)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"message": "Excel parsing failed", "errors": exc.errors},
        ) from exc
    except ValueError as exc:
        log.error("excel_value_error", error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc

    if not tl_rows:
        log.warning("excel_no_valid_rows")
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Excel file contains no valid employee rows.",
        )

    # ── 2. Create EvaluationRun ───────────────────────────────────────────────
    eval_repo = EvaluationRepository(db)

    run = await eval_repo.create(
        EvaluationRun(
            year=year,
            month=month,
            team=team,
            status=EvaluationStatus.RUNNING,
            triggered_by="api",
        )
    )
    await db.commit()
    await db.refresh(run)
    run_id: int = run.id
    log = log.bind(run_id=run_id)
    log.info("evaluation_run_created", employee_count=len(tl_rows))

    # ── 3. Upsert employees and TL assessment scores ──────────────────────────
    log.info("upserting_employees_and_tl_scores")
    emp_repo = EmployeeRepository(db)
    tl_repo = TLAssessmentRepository(db)

    for tl_row in tl_rows:
        # Upsert Employee
        emp = await emp_repo.get_by_employee_id(tl_row.employee_id)
        if emp is None:
            emp = await emp_repo.create(
                Employee(
                    employee_id=tl_row.employee_id,
                    name=tl_row.name,
                    email=tl_row.email,
                    team=team,
                    gitlab_username=tl_row.gitlab_username,
                    is_active=True,
                )
            )
        else:
            # Update mutable fields in place
            emp.name = tl_row.name
            emp.email = tl_row.email
            emp.team = team
            if tl_row.gitlab_username:
                emp.gitlab_username = tl_row.gitlab_username

        # Upsert TLAssessmentScore (check before insert to avoid duplicate)
        existing_tl = await tl_repo.get_by_run_and_email(
            run_id=run_id, email=tl_row.email
        )
        if existing_tl is None:
            await tl_repo.create(
                TLAssessmentScore(
                    evaluation_run_id=run_id,
                    employee_email=tl_row.email,
                    year=year,
                    month=month,
                    problem_solving=tl_row.tl_problem_solving,
                    kpi=tl_row.tl_kpi,
                    general=tl_row.tl_general,
                    total=tl_row.tl_total,
                    uploaded_by="api",
                )
            )
            log.debug("tl_score_saved", email=tl_row.email, total=tl_row.tl_total)
        else:
            existing_tl.problem_solving = tl_row.tl_problem_solving
            existing_tl.kpi = tl_row.tl_kpi
            existing_tl.general = tl_row.tl_general
            existing_tl.total = tl_row.tl_total

    await db.commit()

    # ── 4. Run scorer per employee ────────────────────────────────────────────
    log.info("starting_scorer", team=team)
    try:
        scorer = get_scorer(team)
        log.info("scorer_loaded", scorer=type(scorer).__name__)
    except ValueError as exc:
        log.error("scorer_not_found", team=team, error=str(exc))
        await eval_repo.mark_failed(run, str(exc))
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    processed_count = 0
    failed_count = 0
    emails: list[str] = []
    scoring_errors: list[dict] = []

    for tl_row in tl_rows:
        emp = await emp_repo.get_by_employee_id(tl_row.employee_id)
        if emp is None:
            failed_count += 1
            scoring_errors.append(
                {
                    "email": tl_row.email,
                    "error": "Employee record not found after upsert",
                }
            )
            continue

        log.info("scoring_employee", employee_id=tl_row.employee_id, email=tl_row.email)
        try:
            result = await scorer.calculate(
                employee=emp,
                evaluation_run_id=run_id,
                year=year,
                month=month,
                db=db,
            )
            await db.commit()

            if result.get("error"):
                log.warning(
                    "scorer_returned_error",
                    email=tl_row.email,
                    error=result["error"],
                )
                failed_count += 1
                scoring_errors.append({"email": tl_row.email, "error": result["error"]})
            else:
                processed_count += 1
                emails.append(tl_row.email)
                log.info(
                    "employee_scored",
                    email=tl_row.email,
                    final_score=result.get("final_score"),
                    segment_a=result.get("segment_a_marks"),
                    segment_b=result.get("segment_b_marks"),
                    base_total=result.get("base_total"),
                    reward=result.get("reward_score"),
                )

        except Exception as exc:
            log.exception("scoring_exception", email=tl_row.email, error=str(exc))
            await db.rollback()
            failed_count += 1
            scoring_errors.append({"email": tl_row.email, "error": str(exc)})

    # ── 5. Mark run complete ──────────────────────────────────────────────────
    partial = failed_count > 0
    log.info(
        "scoring_complete",
        processed=processed_count,
        failed=failed_count,
        partial=partial,
    )
    if processed_count == 0:
        await eval_repo.mark_failed(run, f"All {len(tl_rows)} employees failed scoring")
    else:
        await eval_repo.mark_completed(run, partial=partial)
    await db.commit()

    if processed_count == 0:
        return {
            "status": "failed",
            "run_id": run_id,
            "processed_count": processed_count,
            "failed_count": failed_count,
            "errors": scoring_errors,
            "report_path": None,
        }

    # ── 6. Generate Excel report ──────────────────────────────────────────────
    report_path: str | None = None
    log.info("generating_excel_report", emails=emails)
    try:
        report_path = await generate_excel_report(
            run_id=run_id,
            emails=emails,
            team=team,
            year=year,
            month=month,
            db=db,
        )
        log.info("excel_report_saved", path=report_path)
    except Exception as exc:
        log.exception("report_generation_failed", run_id=run_id, error=str(exc))
        report_path = None
        scoring_errors.append({"email": "report_generation", "error": str(exc)})

    # ── 7. Generate Code Quality detail report ────────────────────────────────
    cq_report_path: str | None = None
    if team == "developer":
        log.info("generating_code_quality_report", emails=emails)
        try:
            cq_report_path = await generate_code_quality_report(
                run_id=run_id,
                emails=emails,
                team=team,
                year=year,
                month=month,
                db=db,
            )
            log.info("cq_report_saved", path=cq_report_path)
        except Exception as exc:
            log.exception("cq_report_generation_failed", run_id=run_id, error=str(exc))
            cq_report_path = None
            scoring_errors.append({"email": "cq_report_generation", "error": str(exc)})

    # ── 8. Return summary ─────────────────────────────────────────────────────
    summary = {
        "status": "partial" if partial else "success",
        "run_id": run_id,
        "team": team,
        "year": year,
        "month": month,
        "processed_count": processed_count,
        "failed_count": failed_count,
        "report_path": report_path,
        "cq_report_path": cq_report_path,
        "errors": scoring_errors if scoring_errors else [],
    }
    log.info("bulk_run_complete", **{k: v for k, v in summary.items() if k != "errors"})
    return summary
