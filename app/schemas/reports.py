"""
app/schemas/reports.py
───────────────────────
Schemas for performance reports returned by the reporting API.
"""

from datetime import datetime

from pydantic import BaseModel


class EmployeeReportResponse(BaseModel):
    employee_email: str
    employee_name: str
    team: str
    year: int
    month: int

    # Score breakdown
    quality_check_score: float
    work_log_hours: float
    work_log_score: float
    sentiment_score: float
    component2_score: float
    segment_a_marks: float

    attendance_score: float
    attendance_marks: float
    problem_solving: float
    kpi: float
    general_assessment: float
    tl_total: float
    segment_b_marks: float

    base_total: float
    reward_score: float
    final_score: float

    generated_at: datetime


class TeamReportEntry(BaseModel):
    employee_email: str
    employee_name: str
    final_score: float
    segment_a_marks: float
    segment_b_marks: float
    reward_score: float


class TeamReportResponse(BaseModel):
    team: str
    year: int
    month: int
    employees: list[TeamReportEntry]
    team_average: float
    generated_at: datetime
