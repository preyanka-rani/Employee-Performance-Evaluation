"""
app/teams/support/team.py
─────────────────────────
SupportTeam — the LangGraph-backed implementation of the TeamContract
for the four support sub-teams (impl_its, onsite_support, production,
tech_support).

This module is fully self-contained — all support-specific logic
(formulas, LangGraph state machine, Excel report generator) lives
inside ``app/teams/support/`` so this team is completely isolated
from the rest of the system.

    run_support_evaluation()  ← app/teams/support/graph.py
        │
        ▼
    SupportTeam.run_per_employee()  ← this file
        │  • resolves employee record
        │  • reads TL scores from the CanonicalRow
        │  • picks the concrete sub-team from ctx["team_key"]
        │  • uses bulk-prefetched data from ctx["extra"] when available
        │
        ▼
    Report: outputs/support/Support_Final_Report_<sub-team>_<YYYY>_<MM>.xlsx

Sub-team routing
────────────────
The team contract is shared across four sub-teams — they all use the
same scoring formulas, the same DB tables, the same Excel report
template. The only thing that changes is the *sub-team key* (and the
filename embedded in the report). We resolve the sub-team key from
``ctx["team_key"]`` (which the orchestrator set via TeamRegistry).

Bulk pre-fetch
──────────────
The legacy implementation already supports a batch-fetch optimisation:
``run_support_evaluation`` accepts ``prefetched_crm_log_records``,
``prefetched_ticket_records``, ``prefetched_attendance_records`` so a
team-wide MySQL query can be reused across every employee. We expose
this via ``SupportTeam.pre_fetch_bulk()`` and the orchestrator stores
the result on the per-employee ``TeamContext["extra"]``.
"""

from __future__ import annotations

from typing import Any, ClassVar

from langgraph.graph import StateGraph
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging_config import get_logger
from app.repositories.employee_repository import EmployeeRepository
from app.shared.data_sources.mysql_client import MySQLHRClient
from app.shared.data_sources.support_crm_client import SupportCRMClient
from app.shared.data_sources.support_tickets_client import SupportTicketsClient
from app.shared.excel_parser.row_schema import CanonicalRow
from app.teams.base import TeamContext, TeamContract
from app.teams.support.graph import run_support_evaluation
from app.teams.support.report import generate_support_excel_report

logger = get_logger(__name__)


# All four support sub-team keys + the umbrella "support" alias.
SUPPORT_ALIASES: frozenset[str] = frozenset(
    {"impl_its", "onsite_support", "production", "tech_support", "support"}
)


class SupportTeam(TeamContract):
    """
    Worker for every support sub-team.

    One class, four sub-teams — the sub-team identity lives on the
    ``TeamContext["team_key"]`` which the orchestrator sets per request.
    """

    # ── Class-level configuration ─────────────────────────────────────────────
    team_key: ClassVar[str] = "support"  # umbrella / canonical key
    display_name: ClassVar[str] = "Support"
    aliases: ClassVar[frozenset[str]] = SUPPORT_ALIASES

    graph: ClassVar[StateGraph | None] = None  # wrapped from support_evaluation_workflow

    # ── Bulk pre-fetch (called once per orchestrator request) ─────────────────

    async def pre_fetch_bulk(
        self,
        rows: list[CanonicalRow],
        year: int,
        month: int,
    ) -> dict[str, Any]:
        """
        Issue one team-wide batch query per source and return a dict that
        the orchestrator stores on every per-employee ``TeamContext["extra"]``.

        Returns
        -------
        dict with keys:
            crm_log_records   : list[dict]   (from MySQL CRM)
            ticket_records    : list[dict]   (from MySQL support tickets)
            attendance_records: list[dict]   (from MySQL HR)
        """
        log = logger.bind(year=year, month=month, row_count=len(rows))
        log.info("support_pre_fetch_start")

        # Resolve canonical employee_ids (HR IDs) for every row.
        # We do this in DB-land, not Excel-land, so the team-wide MySQL
        # queries use the same identifiers the workflow expects.
        # NOTE: the orchestrator constructs an AsyncSession-less context for
        # this call, so we open a short-lived session here.
        from app.core.database import AsyncSessionFactory  # local import to avoid cycles

        employee_ids: list[str] = []
        employee_id_by_email: dict[str, str] = {}

        async with AsyncSessionFactory() as db:
            emp_repo = EmployeeRepository(db)
            for row in rows:
                emp = await emp_repo.get_by_employee_id(row.employee_id)
                if emp is None:
                    # Fall back to whatever the row carries — best effort
                    employee_ids.append(row.employee_id)
                    employee_id_by_email[row.employee_email.lower()] = row.employee_id
                else:
                    employee_ids.append(emp.employee_id)
                    employee_id_by_email[emp.email.lower()] = emp.employee_id

            if not employee_ids:
                log.warning("support_pre_fetch_no_employees")
                return {
                    "crm_log_records": [],
                    "ticket_records": [],
                    "attendance_records": [],
                }

            crm = SupportCRMClient()
            tickets = SupportTicketsClient()
            hr = MySQLHRClient()
            try:
                crm_hours, crm_descs = await _gather_crm(crm, employee_ids, year, month)
                ticket_rows = await _safe(
                    tickets.get_ticket_scores,
                    employee_ids=employee_ids,
                    year=year,
                    month=month,
                    label="support_tickets_bulk",
                )
                att_rows = await _safe(
                    hr.get_attendance,
                    employee_ids=employee_ids,
                    year=year,
                    month=month,
                    label="support_hr_bulk",
                )
            finally:
                await _gather_close(crm, tickets, hr)

        # Merge CRM hours and descriptions into the workflow's expected
        # per-employee shape (same format produced by fetch_data_node).
        crm_log_records = _merge_crm(crm_hours, crm_descs, employee_id_by_email)

        log.info(
            "support_pre_fetch_done",
            crm=len(crm_log_records),
            tickets=len(ticket_rows),
            attendance=len(att_rows),
        )
        return {
            "crm_log_records": crm_log_records,
            "ticket_records": ticket_rows,
            "attendance_records": att_rows,
        }

    # ── Per-employee scoring ──────────────────────────────────────────────────

    async def run_per_employee(
        self,
        row: CanonicalRow,
        ctx: TeamContext,
    ) -> dict[str, Any]:
        """
        Score a single support employee by delegating to the existing
        ``run_support_evaluation`` workflow.
        """
        run_id: int = ctx["run_id"]
        year: int = ctx["year"]
        month: int = ctx["month"]
        db: AsyncSession = ctx["db"]
        sub_team: str = ctx["team_key"]

        result: dict[str, Any] = {
            "employee_id": row.employee_id,
            "employee_email": row.employee_email,
            "final_score": 0.0,
            "segment_a_marks": 0.0,
            "segment_b_marks": 0.0,
            "base_total": 0.0,
            "reward_score": 0.0,  # support teams have no reward marks
            "error": None,
        }

        log = logger.bind(
            employee_id=row.employee_id,
            sub_team=sub_team,
            run_id=run_id,
            year=year,
            month=month,
        )
        log.info("support_team_run_start")

        # ── Look up the canonical Employee record ────────────────────────────
        emp_repo = EmployeeRepository(db)
        employee = await emp_repo.get_by_employee_id(row.employee_id)
        if employee is None:
            msg = f"Employee {row.employee_id} not found in DB"
            log.error("employee_not_found")
            result["error"] = msg
            return result

        # ── TL assessment scores come from the canonical row ─────────────────
        # (Excel parser already normalised "support_readiness" or
        #  "problem_solving" into the right CanonicalRow field.)
        tl_support_readiness = float(row.support_readiness or row.problem_solving)
        tl_kpi = float(row.kpi)
        tl_general = float(row.general)

        # ── Pull bulk-prefetched data off the context, if any ────────────────
        extra = ctx.get("extra") or {}

        try:
            state = await run_support_evaluation(
                employee_email=employee.email,
                employee_id=employee.employee_id,
                evaluation_run_id=run_id,
                year=year,
                month=month,
                team=sub_team,
                tl_support_readiness=tl_support_readiness,
                tl_kpi=tl_kpi,
                tl_general=tl_general,
                db=db,
                prefetched_crm_log_records=extra.get("crm_log_records"),
                prefetched_ticket_records=extra.get("ticket_records"),
                prefetched_attendance_records=extra.get("attendance_records"),
            )
        except Exception as exc:
            log.error("support_workflow_failed", error=str(exc))
            result["error"] = str(exc)
            return result

        # ── Surface the workflow results ─────────────────────────────────────
        result.update(
            {
                "final_score": float(state.get("final_score", 0.0)),
                "segment_a_marks": float(state.get("segment_a_marks", 0.0)),
                "segment_b_marks": float(state.get("segment_b_marks", 0.0)),
                "base_total": float(state.get("base_total", 0.0)),
                "reward_score": 0.0,
                "support_readiness": tl_support_readiness,
                "kpi": tl_kpi,
                "general": tl_general,
                "crm_log_score": float(state.get("crm_log_score", 0.0)),
                "tickets_evaluation_score": float(
                    state.get("tickets_evaluation_score", 0.0)
                ),
                "monthly_functional_score": float(
                    state.get("monthly_functional_score", 0.0)
                ),
                "persisted": bool(state.get("persisted", False)),
                "persist_error": state.get("persist_error"),
            }
        )
        if state.get("persist_error"):
            result["error"] = state["persist_error"]

        log.info(
            "support_team_run_done",
            final_score=result["final_score"],
            persisted=result["persisted"],
        )
        return result

    # ── Team-level Excel report ───────────────────────────────────────────────

    async def generate_report(
        self,
        run_id: int,
        emails: list[str],
        team_key: str,
        year: int,
        month: int,
        db: AsyncSession,
        **kwargs: Any,
    ) -> str | None:
        """
        Build the support Excel report and save it to
        ``outputs/support/Support_Final_Report_{sub_team}_{year}_{month:02d}.xlsx``.
        """
        return await generate_support_excel_report(
            run_id=run_id,
            emails=emails,
            team=team_key,
            year=year,
            month=month,
            db=db,
            col_names=kwargs.get("col_names"),
            team_display_name=kwargs.get("team_display_name", ""),
        )


# ── Module-level helpers (kept private to keep team.py tight) ─────────────────


async def _gather_crm(
    crm: SupportCRMClient,
    employee_ids: list[str],
    year: int,
    month: int,
) -> tuple[list, list]:
    import asyncio

    async def _hours() -> list:
        return await _safe(
            crm.get_crm_log_hours,
            employee_ids=employee_ids,
            year=year,
            month=month,
            label="support_crm_hours_bulk",
        )

    async def _descs() -> list:
        return await _safe(
            crm.get_crm_descriptions,
            employee_ids=employee_ids,
            year=year,
            month=month,
            label="support_crm_descs_bulk",
        )

    return await asyncio.gather(_hours(), _descs())


async def _gather_close(*clients: Any) -> None:
    import asyncio

    await asyncio.gather(*(c.close() for c in clients), return_exceptions=True)


async def _safe(fn: Any, *, label: str, **kwargs: Any) -> list:
    """Call *fn* and return [] on any exception (matches fetch_data_node semantics)."""
    try:
        result = await fn(**kwargs)
        return result or []
    except Exception as exc:
        logger.error("support_bulk_fetch_failed", label=label, error=str(exc))
        return []


def _merge_crm(
    crm_hours: list[dict],
    crm_descs: list[dict],
    employee_id_by_email: dict[str, str],
) -> list[dict]:
    """
    Merge CRM hours and descriptions into the workflow's expected shape:

        [{...hours, "descriptions": [str, ...]}, ...]

    grouped by employee email (lowercased).
    """
    descs_by_email: dict[str, list[str]] = {}
    for d in crm_descs:
        email = (d.get("user_email") or "").lower()
        descs_by_email.setdefault(email, []).append(d.get("description", ""))

    merged: list[dict] = []
    for h in crm_hours:
        email = (h.get("user_email") or "").lower()
        if not email:
            continue
        merged.append(
            {
                **h,
                "descriptions": descs_by_email.get(email, []),
                "employee_id": h.get("employee_id")
                or employee_id_by_email.get(email, ""),
            }
        )
    return merged
