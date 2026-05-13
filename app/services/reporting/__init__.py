# app/services/reporting/__init__.py
from app.services.reporting.report_generator import (
    generate_employee_report,
    generate_team_report,
)

__all__ = ["generate_employee_report", "generate_team_report"]
