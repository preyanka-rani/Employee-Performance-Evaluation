"""
app/api/v1/support_evaluations.py
────────────────────────────────────
Core support evaluation logic for all non-developer teams
(Impl & ITS, Onsite Support, Production, Tech Support).

This module exposes a single callable function ``execute_support_bulk_run``
that is invoked by the unified POST /api/v1/evaluations/bulk-run endpoint
in evaluations.py whenever a non-developer team is requested.

No HTTP routes are defined here.
"""

from __future__ import annotations

import asyncio

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging_config import get_logger
from app.repositories.evaluation_repository import EvaluationRepository
from app.schemas.support_evaluation import SupportEmployeeResult

_log = get_logger(__name__)


async def execute_support_bulk_run(
    team_key: str,
    year: int,
    month: int,
    file_bytes: bytes,
    filename: str,
    db: AsyncSession,
) -> dict:
    """
    Core support evaluation logic. Called from the unified
    POST /api/v1/evaluations/bulk-run endpoint.

    Workflow:
        1. Parse uploaded TL assessment Excel (static → AI fallback)
        2. Resolve missing employee IDs via MySQL CRM lookup
        3. Create EvaluationRun record (status=RUNNING)
        4. Upsert Employee + TLAssessmentScore rows in SQLite
        5. Run scorer per employee
        6. Mark run COMPLETED / PARTIAL / FAILED
        7. Generate Excel report in outputs/reports/
        8. Return summary dict
    """
    from app.models.employee import Employee
    from app.models.evaluation_run import EvaluationRun, EvaluationStatus
    from app.models.scores import TLAssessmentScore
    from app.repositories.employee_repository import EmployeeRepository
    from app.repositories.score_repository import TLAssessmentRepository
    from app.services.scoring.base import get_scorer
    from app.services.support_teams.excel_parser.parser import (
        SupportExcelParseError,
        parse_support_tl_excel,
    )
    from app.services.support_teams.reporting.report_generator import (
        generate_support_excel_report,
    )

    log = _log.bind(team=team_key, year=year, month=month)

    # ── 1. Parse Excel (async – may call AI for column mapping) ──────────────
    log.info("support_bulk_run_start", filename=filename)
    try:
        parse_result = await parse_support_tl_excel(file_bytes)
    except SupportExcelParseError as exc:
        log.error("support_excel_parse_error", errors=exc.errors)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"message": "Excel parsing failed", "errors": exc.errors},
        ) from exc

    tl_rows = parse_result.rows
    parse_errors = parse_result.errors
    # Original column names from the uploaded Excel (canonical → original header)
    col_names: dict[str, str] = parse_result.col_names
    # Team display name: taken from the first row’s parsed team_name cell; falls
    # back to the normalised team_key when the Excel has no team column.
    team_display_name: str = (
        tl_rows[0].team_name if (tl_rows and tl_rows[0].team_name) else team_key
    )

    if parse_errors:
        log.warning("support_excel_parse_warnings", warnings=parse_errors)

    if not tl_rows:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Excel file contains no valid employee rows.",
        )

    log.info("support_excel_parsed", row_count=len(tl_rows), warnings=len(parse_errors))

    # ── 3. Resolve missing employee IDs via MySQL CRM ─────────────────────────
    missing_id_emails = [r.employee_email for r in tl_rows if not r.employee_id]
    if missing_id_emails:
        from app.services.support_teams.data_sources.crm_client import (  # noqa: PLC0415
            SupportCRMClient,
        )

        crm = SupportCRMClient()
        try:
            resolved_ids: dict[str, str] = await crm.get_employee_ids_by_emails(
                missing_id_emails
            )
        except Exception as exc:
            log.warning(
                "mysql_employee_id_lookup_failed",
                error=str(exc),
                emails=missing_id_emails,
            )
            resolved_ids = {}
        finally:
            try:
                await crm.close()
            except Exception:
                pass

        for row in tl_rows:
            if not row.employee_id:
                found = resolved_ids.get(row.employee_email)
                if found:
                    row.employee_id = found

    # Fall back to using email as ID for any still-unresolved employees
    for row in tl_rows:
        if not row.employee_id:
            row.employee_id = row.employee_email

    log.info(
        "employee_id_resolution_complete",
        with_id=sum(1 for r in tl_rows if r.employee_id != r.employee_email),
        fallback_to_email=sum(1 for r in tl_rows if r.employee_id == r.employee_email),
    )

    # ── 4. Create EvaluationRun ───────────────────────────────────────────────
    eval_repo = EvaluationRepository(db)
    run = await eval_repo.create(
        EvaluationRun(
            year=year,
            month=month,
            team=team_key,
            status=EvaluationStatus.RUNNING,
            triggered_by="api",
        )
    )
    await db.commit()
    await db.refresh(run)
    run_id: int = run.id
    log = log.bind(run_id=run_id)
    log.info("support_evaluation_run_created", employee_count=len(tl_rows))

    # ── 5. Upsert Employee + TLAssessmentScore rows ───────────────────────────
    emp_repo = EmployeeRepository(db)
    tl_repo = TLAssessmentRepository(db)

    for row in tl_rows:
        # Upsert Employee
        emp = await emp_repo.get_by_email(row.employee_email)
        if emp is None:
            emp = await emp_repo.create(
                Employee(
                    employee_id=row.employee_id,
                    name=row.employee_name or row.employee_email,
                    email=row.employee_email,
                    team=team_key,
                    is_active=True,
                )
            )
        else:
            emp.team = team_key
            if row.employee_id and emp.employee_id != row.employee_id:
                emp.employee_id = row.employee_id
            if row.employee_name and (not emp.name or emp.name == emp.email):
                emp.name = row.employee_name

        # Upsert TLAssessmentScore
        # For support teams: support_readiness maps to TLAssessmentScore.problem_solving
        existing_tl = await tl_repo.get_by_run_and_email(
            run_id=run_id, email=row.employee_email
        )
        if existing_tl is None:
            await tl_repo.create(
                TLAssessmentScore(
                    evaluation_run_id=run_id,
                    employee_email=row.employee_email,
                    year=year,
                    month=month,
                    problem_solving=row.support_readiness,  # reuse problem_solving column
                    kpi=row.kpi,
                    general=row.general,
                    total=row.support_readiness + row.kpi + row.general,
                    uploaded_by="api",
                )
            )
        else:
            existing_tl.problem_solving = row.support_readiness
            existing_tl.kpi = row.kpi
            existing_tl.general = row.general
            existing_tl.total = row.support_readiness + row.kpi + row.general

    await db.commit()
    log.info("support_tl_scores_upserted")

    # ── 6. Run scorer per employee ────────────────────────────────────────────
    scorer = get_scorer(team_key)
    log.info("support_scorer_loaded", scorer=type(scorer).__name__)

    # ── 6a. Batch-fetch all MySQL data for the whole team at once ─────────────
    # This reduces MySQL load from N×3 queries to 3 queries total, preventing
    # max_queries_per_hour / max_connections_per_hour exhaustion.
    all_employee_ids = [r.employee_id for r in tl_rows if r.employee_id]

    from app.services.support_teams.data_sources.crm_client import (  # noqa: PLC0415
        SupportCRMClient,
    )
    from app.services.support_teams.data_sources.tickets_client import (  # noqa: PLC0415
        SupportTicketsClient,
    )
    from app.services.data_sources.mysql_client import MySQLHRClient  # noqa: PLC0415

    batch_crm_hours: list[dict] = []
    batch_crm_descs: list[dict] = []
    batch_tickets: list[dict] = []
    batch_attendance: list[dict] = []

    if all_employee_ids:
        crm_client = SupportCRMClient()
        tickets_client = SupportTicketsClient()
        hr_client = MySQLHRClient()
        try:
            batch_crm_hours, batch_crm_descs, batch_tickets, batch_attendance = (
                await asyncio.gather(
                    crm_client.get_crm_log_hours(all_employee_ids, year, month),
                    crm_client.get_crm_descriptions(all_employee_ids, year, month),
                    tickets_client.get_ticket_scores(all_employee_ids, year, month),
                    hr_client.get_attendance(all_employee_ids, year, month),
                    return_exceptions=False,
                )
            )
            log.info(
                "support_batch_mysql_fetch_done",
                crm_hours=len(batch_crm_hours),
                crm_descs=len(batch_crm_descs),
                tickets=len(batch_tickets),
                attendance=len(batch_attendance),
            )
        except Exception as exc:
            # Non-fatal: fall back to per-employee fetch inside the workflow
            log.warning(
                "support_batch_mysql_fetch_failed",
                error=str(exc),
                fallback="per_employee_fetch",
            )
            batch_crm_hours = []
            batch_crm_descs = []
            batch_tickets = []
            batch_attendance = []
        finally:
            await asyncio.gather(
                crm_client.close(),
                tickets_client.close(),
                hr_client.close(),
                return_exceptions=True,
            )

    # Build pre-fetched CRM log records (hours merged with descriptions per email)
    # keyed by lowercase email so the scoring loop can look them up in O(1).
    _crm_hours_by_id: dict[str, dict] = {
        r["employee_id"]: r for r in batch_crm_hours if r.get("employee_id")
    }
    _crm_descs_by_id: dict[str, list[str]] = {}
    for d in batch_crm_descs:
        eid = d.get("employee_id") or ""
        _crm_descs_by_id.setdefault(eid, []).append(d.get("description", ""))

    def _crm_log_records_for(employee_id: str, email: str) -> list[dict]:
        h = _crm_hours_by_id.get(employee_id)
        if h is None:
            return []
        return [{**h, "descriptions": _crm_descs_by_id.get(employee_id, [])}]

    # Ticket records per email
    _tickets_by_email: dict[str, dict] = {
        r["user_email"].lower(): r for r in batch_tickets if r.get("user_email")
    }

    def _ticket_records_for(email: str) -> list[dict]:
        row = _tickets_by_email.get(email.lower())
        return [row] if row else []

    # Attendance records per email
    _att_by_email: dict[str, dict] = {
        r.get("user_email", "").lower(): r
        for r in batch_attendance
        if r.get("user_email")
    }

    def _attendance_records_for(email: str) -> list[dict]:
        row = _att_by_email.get(email.lower())
        return [row] if row else []

    use_batch = bool(
        all_employee_ids and (batch_crm_hours or batch_tickets or batch_attendance)
    )

    processed_count = 0
    failed_count = 0
    result_list: list[SupportEmployeeResult] = []
    scoring_errors: list[dict] = []
    successful_emails: list[str] = []

    for row in tl_rows:
        emp = await emp_repo.get_by_email(row.employee_email)
        if emp is None:
            failed_count += 1
            scoring_errors.append(
                {
                    "email": row.employee_email,
                    "error": "Employee record not found after upsert",
                }
            )
            continue

        log.info("scoring_support_employee", email=row.employee_email)
        try:
            if use_batch:
                result = await scorer.calculate(
                    employee=emp,
                    evaluation_run_id=run_id,
                    year=year,
                    month=month,
                    db=db,
                    prefetched_crm_log_records=_crm_log_records_for(
                        row.employee_id or "", row.employee_email
                    ),
                    prefetched_ticket_records=_ticket_records_for(row.employee_email),
                    prefetched_attendance_records=_attendance_records_for(
                        row.employee_email
                    ),
                )
            else:
                result = await scorer.calculate(
                    employee=emp,
                    evaluation_run_id=run_id,
                    year=year,
                    month=month,
                    db=db,
                )
            await db.commit()
        except Exception as exc:
            log.error(
                "support_scorer_exception", email=row.employee_email, error=str(exc)
            )
            failed_count += 1
            scoring_errors.append({"email": row.employee_email, "error": str(exc)})
            continue

        if result.get("error"):
            log.warning(
                "support_scorer_error", email=row.employee_email, error=result["error"]
            )
            failed_count += 1
            scoring_errors.append(
                {"email": row.employee_email, "error": result["error"]}
            )
        else:
            processed_count += 1
            successful_emails.append(row.employee_email)
            log.info(
                "support_employee_scored",
                email=row.employee_email,
                final_score=result.get("final_score"),
            )

        result_list.append(
            SupportEmployeeResult(
                employee_id=result.get("employee_id") or row.employee_id or "",
                employee_email=row.employee_email,
                total_log_hours=result.get("total_log_hours", 0.0),
                log_hours_score=result.get("log_hours_score", 0.0),
                sentiment_score=result.get("sentiment_score", 0.0),
                crm_log_score=result.get("crm_log_score", 0.0),
                total_tickets=result.get("total_tickets", 0),
                average_taken_days=result.get("average_taken_days", 0.0),
                tickets_evaluation_score=result.get("tickets_evaluation_score", 0.0),
                monthly_functional_score=result.get("monthly_functional_score", 0.0),
                segment_a_marks=result.get("segment_a_marks", 0.0),
                attendance_score=result.get("attendance_score", 0.0),
                attendance_marks=result.get("attendance_marks", 0.0),
                tl_support_readiness=result.get(
                    "tl_support_readiness", row.support_readiness
                ),
                tl_kpi=result.get("tl_kpi", row.kpi),
                tl_general=result.get("tl_general", row.general),
                tl_total=result.get("tl_total", 0.0),
                segment_b_marks=result.get("segment_b_marks", 0.0),
                base_total=result.get("base_total", 0.0),
                reward_score=0.0,
                final_score=result.get("final_score", 0.0),
                error=result.get("error"),
            )
        )

    # ── 7. Mark run status ────────────────────────────────────────────────────
    if processed_count == 0:
        final_status = EvaluationStatus.FAILED
        run.error_message = "All employees failed scoring."
    elif failed_count > 0:
        final_status = EvaluationStatus.PARTIAL
    else:
        final_status = EvaluationStatus.COMPLETED

    run.status = final_status
    await db.commit()
    log.info("support_run_status_updated", status=final_status.value)

    # ── 8. Generate Excel report ──────────────────────────────────────────────
    report_path: str | None = None
    if successful_emails:
        try:
            report_path = await generate_support_excel_report(
                run_id=run_id,
                emails=successful_emails,
                team=team_key,
                year=year,
                month=month,
                db=db,
                col_names=col_names,
                team_display_name=team_display_name,
            )
            log.info("support_report_generated", path=report_path)
        except Exception as exc:
            log.error("support_report_generation_failed", error=str(exc))

    return {
        "run_id": run_id,
        "team": team_key,
        "year": year,
        "month": month,
        "status": final_status.value,
        "processed_count": processed_count,
        "failed_count": failed_count,
        "report_path": report_path,
        "results": result_list,
        "errors": scoring_errors,
    }
