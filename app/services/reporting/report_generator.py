"""
app/services/reporting/report_generator.py
────────────────────────────────────────────
Compiles all individual score rows into structured report responses.

This module does not calculate scores — it only reads from the DB and
assembles the response schemas for the report endpoints.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.employee_repository import EmployeeRepository
from app.repositories.score_repository import (
    AttendanceRepository,
    CodeQualityRepository,
    FinalScoreRepository,
    SentimentRepository,
    TLAssessmentRepository,
    WorkLogRepository,
)
from app.schemas.reports import (
    EmployeeReportResponse,
    TeamReportEntry,
    TeamReportResponse,
)


async def generate_employee_report(
    employee_id: str,
    year: int,
    month: int,
    db: AsyncSession,
) -> EmployeeReportResponse | None:
    """
    Assemble the full score breakdown for one employee.

    Returns None if the employee or their evaluation data doesn't exist.
    """
    emp_repo = EmployeeRepository(db)
    employee = await emp_repo.get_by_employee_id(employee_id)
    if employee is None:
        return None

    fs_repo = FinalScoreRepository(db)
    score = await fs_repo.get_by_email_and_period(
        email=employee.email,
        year=year,
        month=month,
    )
    if score is None:
        return None

    return EmployeeReportResponse(
        employee_id=employee.employee_id,
        name=employee.name,
        email=employee.email,
        team=employee.team,
        year=year,
        month=month,
        quality_check_score=score.quality_check_score,
        work_log_score=score.work_log_score,
        sentiment_score=score.sentiment_score,
        attendance_score=score.attendance_score,
        problem_solving_score=score.problem_solving_score,
        kpi_score=score.kpi_score,
        general_score=score.general_score,
        segment_a_marks=score.segment_a_marks,
        segment_b_marks=score.segment_b_marks,
        base_total=score.base_total,
        reward_score=score.reward_score,
        final_score=score.final_score,
    )


async def generate_team_report(
    team: str,
    year: int,
    month: int,
    db: AsyncSession,
) -> TeamReportResponse | None:
    """
    Assemble the team report for all active employees in a team.

    Returns None if no employees exist for the team.
    """
    emp_repo = EmployeeRepository(db)
    employees = await emp_repo.get_by_team(team)
    if not employees:
        return None

    emails = [e.email for e in employees]
    email_to_emp = {e.email: e for e in employees}

    fs_repo = FinalScoreRepository(db)
    scores = await fs_repo.get_team_scores_by_period(
        emails=emails,
        year=year,
        month=month,
    )

    entries: list[TeamReportEntry] = []
    for score in scores:
        emp = email_to_emp.get(score.employee_email)
        entries.append(
            TeamReportEntry(
                employee_id=emp.employee_id if emp else "unknown",
                name=emp.name if emp else "unknown",
                email=score.employee_email,
                final_score=score.final_score,
                segment_a_marks=score.segment_a_marks,
                segment_b_marks=score.segment_b_marks,
                base_total=score.base_total,
                reward_score=score.reward_score,
            )
        )

    entries.sort(key=lambda e: e.final_score, reverse=True)
    team_avg = (
        round(sum(e.final_score for e in entries) / len(entries), 2) if entries else 0.0
    )

    return TeamReportResponse(
        team=team,
        year=year,
        month=month,
        employee_count=len(entries),
        team_average_score=team_avg,
        entries=entries,
    )


async def generate_excel_report(
    run_id: int,
    emails: list[str],
    team: str,
    year: int,
    month: int,
    db: AsyncSession,
) -> str:
    """
    Build a formatted Excel report for a completed bulk evaluation run.

    Columns (one row per employee):
        Employee ID | Name | Email | Quality Check | Work Log | Attendance |
        Problem Solving | KPI | General | Seg-A Marks | Seg-B Marks |
        Base Total | Reward | Final Score

    Color coding on Final Score column:
        ≥ 80  → green fill
        60–79 → yellow fill
        < 60  → red fill

    Saves to:  outputs/reports/Final_Report_{team}_{year}_{month:02d}.xlsx

    Returns the saved file path as a string.
    """
    import pathlib

    import openpyxl
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    fs_repo = FinalScoreRepository(db)
    emp_repo = EmployeeRepository(db)

    scores = await fs_repo.get_team_scores(run_id=run_id, emails=emails)
    employees = await emp_repo.get_by_team(team)
    email_to_emp = {e.email: e for e in employees}

    # ── Workbook setup ────────────────────────────────────────────────────────
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = f"{team} {year}-{month:02d}"

    header_font = Font(bold=True, color="FFFFFF")
    header_fill = PatternFill("solid", fgColor="1F4E79")
    header_alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)

    green_fill = PatternFill("solid", fgColor="C6EFCE")
    yellow_fill = PatternFill("solid", fgColor="FFEB9C")
    red_fill = PatternFill("solid", fgColor="FFC7CE")

    headers = [
        "Employee ID",
        "Name",
        "Email",
        "Component 1 Score (0-100)",
        "  Code Quality (30%)",
        "  Resolution Rate % (35%)",
        "  Reopen Quality (15%)",
        "  Lines Added Score (10%)",
        "  Lines Deleted Score (10%)",
        "Work Log Score (0-100)",
        "Attendance Score (0-100)",
        "Problem Solving (0-10)",
        "KPI (0-15)",
        "General Assessment (0-15)",
        "Segment A Marks (0-50)",
        "Segment B Marks (0-50)",
        "Base Total (0-100)",
        "Reward Score (0-5)",
        "Final Score",
    ]
    ws.append(headers)

    # Style header row
    for col_idx, _ in enumerate(headers, start=1):
        cell = ws.cell(row=1, column=col_idx)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = header_alignment

    # Freeze header row
    ws.freeze_panes = "A2"

    # ── Data rows ─────────────────────────────────────────────────────────────
    for score in sorted(scores, key=lambda s: s.final_score, reverse=True):
        emp = email_to_emp.get(score.employee_email)
        row_data = [
            emp.employee_id if emp else "",
            emp.name if emp else "",
            score.employee_email,
            round(score.component1_score, 2),
            round(score.quality_check_score, 2),
            round(score.resolution_rate, 2),
            round(score.reopen_quality_score, 2),
            round(score.lines_added_score, 2),
            round(score.lines_deleted_score, 2),
            round(score.component2_score, 2),
            round(score.attendance_score, 2),
            round(score.problem_solving, 2),
            round(score.kpi, 2),
            round(score.general_assessment, 2),
            round(score.segment_a_marks, 2),
            round(score.segment_b_marks, 2),
            round(score.base_total, 2),
            round(score.reward_score, 2),
            round(score.final_score, 2),
        ]
        ws.append(row_data)

        # Color-code the Final Score cell (last column)
        final_cell = ws.cell(row=ws.max_row, column=len(headers))
        final_val = score.final_score
        if final_val >= 80:
            final_cell.fill = green_fill
        elif final_val >= 60:
            final_cell.fill = yellow_fill
        else:
            final_cell.fill = red_fill

    # ── Column widths ─────────────────────────────────────────────────────────
    column_widths = [
        14,
        22,
        30,
        24,
        22,
        24,
        22,
        22,
        22,
        22,
        22,
        22,
        14,
        22,
        22,
        22,
        20,
        18,
        15,
    ]
    for col_idx, width in enumerate(column_widths, start=1):
        ws.column_dimensions[get_column_letter(col_idx)].width = width

    # ── Summary row ───────────────────────────────────────────────────────────
    if scores:
        avg_final = round(sum(s.final_score for s in scores) / len(scores), 2)
        ws.append([""] * (len(headers) - 2) + ["Team Average", avg_final])
        summary_row = ws.max_row
        for col_idx in range(1, len(headers) + 1):
            ws.cell(row=summary_row, column=col_idx).font = Font(bold=True)

    # ── Save file ─────────────────────────────────────────────────────────────
    output_dir = pathlib.Path("outputs/reports")
    output_dir.mkdir(parents=True, exist_ok=True)
    filename = f"Final_Report_{team}_{year}_{month:02d}.xlsx"
    output_path = output_dir / filename
    wb.save(str(output_path))

    return str(output_path)
