"""
app/orchestrator/nodes.py
─────────────────────────
Node implementations for the supervisor graph.

Each function is an async coroutine that takes the current
``OrchestratorState`` and returns a partial dict that LangGraph merges
into the running state.

Nodes are *sequential* in the early stage (parse → resolve → upsert →
create_run → pre_fetch), then fan out via a conditional edge to one
of two worker nodes (``score_developer`` or ``score_support``), then
converge on ``generate_report`` → ``finalise_run`` → ``build_response``.

Why nodes are pure-ish
──────────────────────
We deliberately do NOT mutate ``state`` in place; we return partial
dicts. This keeps the supervisor testable and makes the data flow
explicit. The only stateful side-effect is the ``db`` session, which
is carried on ``state["db"]`` (not serialised in any checkpoint).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging_config import get_logger
from app.models.employee import Employee
from app.models.evaluation_run import EvaluationRun, EvaluationStatus
from app.models.scores import TLAssessmentScore
from app.repositories.employee_repository import EmployeeRepository
from app.repositories.evaluation_repository import EvaluationRepository
from app.repositories.score_repository import TLAssessmentRepository
from app.shared.data_sources.mysql_client import MySQLCRMClient
from app.shared.excel_parser.parser import ExcelParseError, parse_tl_excel
from app.shared.excel_parser.row_schema import CanonicalRow
from app.shared.persistence.run_orchestrator import RunOrchestrator
from app.shared.registry import TeamRegistry, TEAMS
from app.shared.state import OrchestratorState
from app.teams.base import TeamContext

logger = get_logger(__name__)


# ═════════════════════════════════════════════════════════════════════════════
# 1. PARSE EXCEL
# ═════════════════════════════════════════════════════════════════════════════


async def parse_excel_node(state: OrchestratorState) -> dict[str, Any]:
    """
    Parse the uploaded Excel bytes into CanonicalRow objects.

    Inputs:  state["file_bytes"], state["raw_team_input"]
    Outputs: state["parsed_rows"], state["team_key"], state["team_display_name"],
             state["parse_warnings"], state["col_names"]
    """
    raw_team = state.get("raw_team_input", "")
    log = logger.bind(team=raw_team, year=state.get("year"), month=state.get("month"))
    log.info("parse_excel_start")

    # Resolve team_key from the human-readable name (e.g. "Implementation & I.T.S")
    try:
        team_key = TeamRegistry.resolve(raw_team)
    except ValueError as exc:
        log.error("team_resolve_failed", error=str(exc))
        return {"team_key": "", "parsed_rows": [], "parse_warnings": [str(exc)]}

    team_cls = TEAMS[team_key]
    team_display_name = team_cls.display_name

    try:
        result = await parse_tl_excel(state["file_bytes"], team_key=team_key)
    except ExcelParseError as exc:
        log.error("parse_failed", errors=exc.errors)
        return {
            "team_key": team_key,
            "team_display_name": team_display_name,
            "parsed_rows": [],
            "parse_warnings": exc.errors,
        }

    # Prefer the team_name from the uploaded Excel (e.g. "Impl&ITS") so the
    # generated report preserves the original wording. Fall back to the
    # class display name only if no row carries a team_name.
    if result.rows and any(r.team_name for r in result.rows):
        team_display_name = next(
            (r.team_name for r in result.rows if r.team_name),
            team_display_name,
        )

    log.info(
        "parse_excel_done",
        rows=len(result.rows),
        warnings=len(result.errors),
        resolved_headers=list(result.col_names.keys()),
    )
    return {
        "team_key": team_key,
        "team_display_name": team_display_name,
        "parsed_rows": result.rows,
        "parse_warnings": result.errors,
        "col_names": result.col_names,
    }


# ═════════════════════════════════════════════════════════════════════════════
# 2. RESOLVE MISSING EMPLOYEE IDS FROM MYSQL
# ═════════════════════════════════════════════════════════════════════════════


async def resolve_employee_ids_node(state: OrchestratorState) -> dict[str, Any]:
    """
    Fill in missing ``employee_id`` values by looking up the email in MySQL.

    Mirrors the legacy ``bulk_run_evaluation`` flow in
    ``app/api/v1/evaluations.py``.  Rows that still have no employee_id
    after the lookup are dropped.
    """
    log = logger.bind(team=state.get("team_key"))
    rows: list[CanonicalRow] = state.get("parsed_rows", [])
    if not rows:
        return {"parsed_rows": []}

    missing = [r.employee_email for r in rows if not r.employee_id]
    if not missing:
        log.info("resolve_employee_ids_none_missing")
        return {}

    log.info("resolve_employee_ids_start", count=len(missing))

    crm = MySQLCRMClient()
    try:
        resolved = await crm.get_employee_ids_by_emails(missing)
    except Exception as exc:
        log.error("mysql_employee_id_lookup_failed", error=str(exc))
        resolved = {}
    finally:
        await crm.close()

    still_missing: list[str] = []
    new_rows: list[CanonicalRow] = []
    for row in rows:
        if row.employee_id:
            new_rows.append(row)
            continue
        found = resolved.get(row.employee_email)
        if found:
            new_rows.append(
                CanonicalRow(
                    employee_id=found,
                    employee_email=row.employee_email,
                    employee_name=row.employee_name,
                    problem_solving=row.problem_solving,
                    support_readiness=row.support_readiness,
                    kpi=row.kpi,
                    general=row.general,
                    gitlab_username=row.gitlab_username,
                    team_name=row.team_name,
                )
            )
        else:
            still_missing.append(row.employee_email)

    log.info(
        "resolve_employee_ids_done",
        resolved=len(resolved),
        still_missing=len(still_missing),
        final_count=len(new_rows),
    )
    return {"parsed_rows": new_rows}


# ═════════════════════════════════════════════════════════════════════════════
# 3. UPSERT EMPLOYEES + TL ASSESSMENT SCORES
# ═════════════════════════════════════════════════════════════════════════════


async def upsert_employees_and_tl_node(
    state: OrchestratorState,
) -> dict[str, Any]:
    """
    Ensure every parsed row has a corresponding Employee and TLAssessmentScore.

    Mirrors legacy steps 2+3 in ``bulk_run_evaluation``.  For support teams
    we use the ``use_support_readiness`` flag on the TL upsert helper so the
    "Support Readiness" Excel column lands in the legacy
    ``TLAssessmentScore.problem_solving`` column.
    """
    log = logger.bind(team=state.get("team_key"))
    db: AsyncSession = state["db"]
    rows: list[CanonicalRow] = state.get("parsed_rows", [])
    year: int = state["year"]
    month: int = state["month"]
    team_key: str = state["team_key"]

    if not rows:
        log.warning("no_rows_to_upsert_skipping_run_creation")
        return {"run_id": 0}  # sentinel — downstream nodes must check

    emp_repo = EmployeeRepository(db)
    tl_repo = TLAssessmentRepository(db)

    # Need a run_id before TL scores can be inserted; create the run now
    # (so we can reuse the same EvaluationRun across both upsert and scoring).
    run_orch = RunOrchestrator(db)
    run = await run_orch.create(year=year, month=month, team=team_key)
    run_id = run.id
    log = log.bind(run_id=run_id)
    log.info("evaluation_run_created", employee_count=len(rows))

    # ── Upsert employees + TL scores ─────────────────────────────────────
    is_support = team_key in ("impl_its", "onsite_support", "production", "tech_support", "support", "cirt_infra")
    from app.shared.persistence.tl_upserter import TLUpserter

    upserter = TLUpserter(db)
    for row in rows:
        await upserter.upsert_employee_and_tl(
            row=row,
            team_key=team_key,
            run_id=run_id,
            year=year,
            month=month,
            use_support_readiness=is_support,
        )

    await db.commit()
    log.info("upsert_employees_done", count=len(rows))
    return {"run_id": run_id}


# ═════════════════════════════════════════════════════════════════════════════
# 4. PRE-FETCH BULK (optimisation for support teams)
# ═════════════════════════════════════════════════════════════════════════════


async def pre_fetch_bulk_node(state: OrchestratorState) -> dict[str, Any]:
    """
    One team-wide batch query per source (CRM, tickets, attendance).

    Stores the result in state under ``state["bulk_data"]`` so every
    per-employee ``run_per_employee`` call can reuse it via
    ``ctx["extra"]``.  Developer team has no pre-fetch (its graph
    fetches per-employee in parallel).
    """
    log = logger.bind(team=state.get("team_key"), run_id=state.get("run_id"))
    team_key: str = state["team_key"]
    team_cls = TEAMS[team_key]
    team = team_cls()

    rows: list[CanonicalRow] = state.get("parsed_rows", [])
    if not rows:
        log.warning("pre_fetch_bulk_no_rows")
        return {"bulk_data": {}}

    year: int = state["year"]
    month: int = state["month"]

    try:
        bulk = await team.pre_fetch_bulk(rows, year=year, month=month)
    except Exception as exc:
        log.warning("pre_fetch_bulk_failed_continuing", error=str(exc))
        bulk = {}

    log.info("pre_fetch_bulk_done", keys=list(bulk.keys()))
    return {"bulk_data": bulk}


# ═════════════════════════════════════════════════════════════════════════════
# 5. SCORE-DEVELOPER NODE
# ═════════════════════════════════════════════════════════════════════════════


async def score_developer_node(state: OrchestratorState) -> dict[str, Any]:
    """Score every employee using the DeveloperTeam worker."""
    return await _score_team_node(state, team_key="developer")


# ═════════════════════════════════════════════════════════════════════════════
# 6. SCORE-SUPPORT NODE
# ═════════════════════════════════════════════════════════════════════════════


async def score_support_node(state: OrchestratorState) -> dict[str, Any]:
    """Score every employee using the SupportTeam worker for the sub-team."""
    return await _score_team_node(state, team_key=state["team_key"])


# ═════════════════════════════════════════════════════════════════════════════
# 6b. SCORE-CIRT-INFRA NODE
# ═════════════════════════════════════════════════════════════════════════════


async def score_cirt_infra_node(state: OrchestratorState) -> dict[str, Any]:
    """Score every employee using the CIRTInfraTeam worker."""
    return await _score_team_node(state, team_key="cirt_infra")


# ── Shared scorer loop ────────────────────────────────────────────────────────


async def _score_team_node(
    state: OrchestratorState,
    *,
    team_key: str,
) -> dict[str, Any]:
    log = logger.bind(team=team_key, run_id=state.get("run_id"))
    rows = state.get("parsed_rows", [])
    log.info("score_team_start", employees=len(rows))

    if not rows:
        log.warning("score_team_no_rows")
        return {
            "processed_count": 0,
            "failed_count": 0,
            "successful_emails": [],
            "scoring_errors": [],
            "team_results": [],
        }

    db: AsyncSession = state["db"]
    run_id: int = state.get("run_id", 0)
    if not run_id:
        log.error("score_team_no_run_id")
        return {
            "processed_count": 0,
            "failed_count": len(rows),
            "successful_emails": [],
            "scoring_errors": [
                {"email": "all", "error": "EvaluationRun was not created (no rows to upsert)."}
            ],
            "team_results": [],
        }

    year: int = state["year"]
    month: int = state["month"]
    bulk_data: dict[str, Any] = state.get("bulk_data", {})

    team_cls = TEAMS[team_key]
    team = team_cls()

    ctx_base = {
        "run_id": run_id,
        "year": year,
        "month": month,
        "db": db,
        "team_display_name": state.get("team_display_name") or team.display_name,
    }

    processed = 0
    failed = 0
    successful_emails: list[str] = []
    errors: list[dict[str, Any]] = []
    results: list[dict[str, Any]] = []

    for row in rows:
        ctx = TeamContext(
            **ctx_base,
            team_key=team_key,
            extra=bulk_data,
        )
        try:
            result = await team.run_per_employee(row, ctx)
            await db.commit()
        except Exception as exc:
            log.exception("scoring_exception", email=row.employee_email, error=str(exc))
            await db.rollback()
            failed += 1
            errors.append({"email": row.employee_email, "error": str(exc)})
            continue

        if result.get("error"):
            failed += 1
            errors.append(
                {"email": row.employee_email, "error": result["error"]}
            )
        else:
            processed += 1
            successful_emails.append(row.employee_email)
            results.append(result)

    log.info(
        "score_team_done",
        processed=processed,
        failed=failed,
    )
    return {
        "processed_count": processed,
        "failed_count": failed,
        "successful_emails": successful_emails,
        "scoring_errors": errors,
        "team_results": results,
    }


# ═════════════════════════════════════════════════════════════════════════════
# 7. GENERATE REPORT
# ═════════════════════════════════════════════════════════════════════════════


async def generate_report_node(state: OrchestratorState) -> dict[str, Any]:
    """
    Build the team-specific Excel report.

    For developer this produces:
        outputs/reports/CodeQuality_Report_developer_{year}_{month:02d}.xlsx
        outputs/reports/Final_Report_developer_{year}_{month:02d}.xlsx

    For support sub-teams:
        outputs/support/Support_Final_Report_{sub_team}_{year}_{month:02d}.xlsx
    """
    log = logger.bind(team=state.get("team_key"), run_id=state.get("run_id"))
    team_key: str = state["team_key"]
    team_cls = TEAMS[team_key]
    team = team_cls()
    db: AsyncSession = state["db"]
    run_id: int = state["run_id"]
    year: int = state["year"]
    month: int = state["month"]
    emails: list[str] = state.get("successful_emails", [])

    if not emails:
        log.warning("generate_report_no_successful_emails")
        return {"report_path": None}

    try:
        report_path = await team.generate_report(
            run_id=run_id,
            emails=emails,
            team_key=team_key,
            year=year,
            month=month,
            db=db,
            col_names=state.get("col_names"),
            team_display_name=state.get("team_display_name", ""),
        )
    except Exception as exc:
        log.exception("generate_report_failed", error=str(exc))
        return {
            "report_path": None,
            "scoring_errors": state.get("scoring_errors", [])
            + [{"email": "report_generation", "error": str(exc)}],
        }

    log.info("generate_report_done", path=report_path)
    return {"report_path": report_path}


# ═════════════════════════════════════════════════════════════════════════════
# 8. FINALISE RUN
# ═════════════════════════════════════════════════════════════════════════════


async def finalise_run_node(state: OrchestratorState) -> dict[str, Any]:
    """Mark the EvaluationRun as COMPLETED / PARTIAL / FAILED."""
    log = logger.bind(run_id=state.get("run_id"))
    db: AsyncSession = state["db"]
    run_id: int = state.get("run_id", 0)
    if not run_id:
        return {}

    processed = state.get("processed_count", 0)
    failed = state.get("failed_count", 0)

    eval_repo = EvaluationRepository(db)
    run = await eval_repo.get_by_id(run_id)
    if run is None:
        log.error("run_not_found_for_finalise")
        return {}

    if processed == 0:
        run.status = EvaluationStatus.FAILED
        run.finished_at = datetime.now(timezone.utc)
        run.error_message = f"All {failed} employees failed scoring."[:1000]
    elif failed > 0:
        run.status = EvaluationStatus.PARTIAL
        run.finished_at = datetime.now(timezone.utc)
    else:
        run.status = EvaluationStatus.COMPLETED
        run.finished_at = datetime.now(timezone.utc)

    await db.commit()
    log.info(
        "run_finalised",
        status=run.status.value,
        processed=processed,
        failed=failed,
    )
    return {}


# ═════════════════════════════════════════════════════════════════════════════
# 9. BUILD RESPONSE
# ═════════════════════════════════════════════════════════════════════════════


async def build_response_node(state: OrchestratorState) -> dict[str, Any]:
    """Compose the final response dict for the API caller."""
    processed = state.get("processed_count", 0)
    failed = state.get("failed_count", 0)
    partial = failed > 0
    rows = state.get("parsed_rows", [])
    run_id = state.get("run_id", 0)

    # If parsed_rows is empty we never created an EvaluationRun.
    if not rows and not run_id:
        summary = {
            "status": "failed",
            "run_id": None,
            "team": state.get("team_key"),
            "year": state.get("year"),
            "month": state.get("month"),
            "processed_count": 0,
            "failed_count": 0,
            "report_path": None,
            "errors": [
                {
                    "email": "all",
                    "error": "No valid rows after employee_id resolution. "
                    "Check that the Excel contains emails that exist in MySQL.",
                }
            ],
        }
        return {"summary": summary}

    summary = {
        "status": "partial" if partial and processed else ("failed" if processed == 0 else "success"),
        "run_id": run_id,
        "team": state.get("team_key"),
        "year": state.get("year"),
        "month": state.get("month"),
        "processed_count": processed,
        "failed_count": failed,
        "report_path": state.get("report_path"),
        "errors": state.get("scoring_errors", []),
    }
    return {"summary": summary}
