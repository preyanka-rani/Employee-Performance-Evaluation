"""
app/teams/developer/report.py
─────────────────────────────
Developer worker report generator.

Thin wrapper over ``app.services.reporting.report_generator`` — the
legacy functions already save their outputs to the canonical
``outputs/developer/`` directory, so this module simply resolves the
absolute paths and surfaces them in the contract's expected shape.

Output paths produced (per period):
    outputs/developer/CodeQuality_Report_developer_{year}_{month:02d}.xlsx
    outputs/developer/Final_Report_developer_{year}_{month:02d}.xlsx

File names are identical to the legacy functions and the directory
follows the team-scoped ``outputs/<team>/`` convention used by the
support team (``outputs/support/``).
"""

from __future__ import annotations

import pathlib

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging_config import get_logger
from app.services.reporting import report_generator

logger = get_logger(__name__)


async def generate_developer_reports(
    *,
    run_id: int,
    emails: list[str],
    year: int,
    month: int,
    db: AsyncSession,
) -> dict[str, str]:
    """
    Generate both developer Excel reports.

    The legacy ``app.services.reporting.report_generator`` writes each
    file directly into ``outputs/developer/`` — this wrapper just calls
    the two generators and resolves the resulting absolute paths.

    Parameters
    ----------
    run_id : int
        Evaluation run id (used to fetch all bundles / scores for that run).
    emails : list[str]
        Employee emails that participated in the run.
    year, month : int
        Evaluation period.
    db : AsyncSession
        Active session for repository lookups.

    Returns
    -------
    dict[str, str]
        Mapping ``"code_quality_report"`` and ``"final_report"`` to absolute
        file paths. Both keys are always present (empty string if the source
        Excel is empty).
    """
    log = logger.bind(run_id=run_id, year=year, month=month, count=len(emails))
    log.info("developer_reports_start")

    # 1. Code-quality report (per-project analysis)
    cq_path = await report_generator.generate_code_quality_report(
        run_id=run_id,
        emails=emails,
        team="developer",
        year=year,
        month=month,
        db=db,
    )

    # 2. Final score report (24-column component breakdown)
    fr_path = await report_generator.generate_excel_report(
        run_id=run_id,
        emails=emails,
        team="developer",
        year=year,
        month=month,
        db=db,
    )

    log.info(
        "developer_reports_done",
        code_quality=cq_path,
        final=fr_path,
    )
    return {
        "code_quality_report": str(pathlib.Path(cq_path).resolve()) if cq_path else "",
        "final_report": str(pathlib.Path(fr_path).resolve()) if fr_path else "",
    }
