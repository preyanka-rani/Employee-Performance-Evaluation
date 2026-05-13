# app/repositories/__init__.py
from app.repositories.employee_repository import EmployeeRepository
from app.repositories.evaluation_repository import EvaluationRepository
from app.repositories.score_repository import (
    AttendanceRepository,
    CodeQualityRepository,
    FinalScoreRepository,
    SentimentRepository,
    TLAssessmentRepository,
    WorkLogRepository,
)

__all__ = [
    "EmployeeRepository",
    "EvaluationRepository",
    "AttendanceRepository",
    "CodeQualityRepository",
    "FinalScoreRepository",
    "SentimentRepository",
    "TLAssessmentRepository",
    "WorkLogRepository",
]
