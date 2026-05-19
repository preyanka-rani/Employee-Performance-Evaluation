"""
app/repositories/score_repository.py
──────────────────────────────────────
Async data-access layer for all scoring tables.
"""

from sqlalchemy import select

from app.models.scores import (
    AttendanceScore,
    CodeQualityScore,
    DeveloperFinalScore,
    FinalScore,
    SentimentScore,
    TLAssessmentScore,
    WorkLogScore,
)
from app.repositories.base import BaseRepository


class CodeQualityRepository(BaseRepository[CodeQualityScore]):
    model = CodeQualityScore

    async def get_by_run_and_email(
        self, run_id: int, email: str
    ) -> list[CodeQualityScore]:
        result = await self._session.execute(
            select(CodeQualityScore).where(
                CodeQualityScore.evaluation_run_id == run_id,
                CodeQualityScore.employee_email == email,
            )
        )
        return list(result.scalars().all())

    async def get_by_run_id_and_emails(
        self, run_id: int, emails: list[str]
    ) -> list[CodeQualityScore]:
        """Fetch all code quality rows for a run filtered to the given email list."""
        result = await self._session.execute(
            select(CodeQualityScore).where(
                CodeQualityScore.evaluation_run_id == run_id,
                CodeQualityScore.employee_email.in_(emails),
            )
        )
        return list(result.scalars().all())

    async def mr_already_analyzed(self, run_id: int, mr_reference: str) -> bool:
        result = await self._session.execute(
            select(CodeQualityScore.id).where(
                CodeQualityScore.evaluation_run_id == run_id,
                CodeQualityScore.mr_reference == mr_reference,
            )
        )
        return result.scalar_one_or_none() is not None


class AttendanceRepository(BaseRepository[AttendanceScore]):
    model = AttendanceScore

    async def get_by_run_and_email(
        self, run_id: int, email: str
    ) -> AttendanceScore | None:
        result = await self._session.execute(
            select(AttendanceScore).where(
                AttendanceScore.evaluation_run_id == run_id,
                AttendanceScore.employee_email == email,
            )
        )
        return result.scalar_one_or_none()


class SentimentRepository(BaseRepository[SentimentScore]):
    model = SentimentScore

    async def get_by_run_and_email(
        self, run_id: int, email: str
    ) -> SentimentScore | None:
        result = await self._session.execute(
            select(SentimentScore).where(
                SentimentScore.evaluation_run_id == run_id,
                SentimentScore.employee_email == email,
            )
        )
        return result.scalar_one_or_none()


class WorkLogRepository(BaseRepository[WorkLogScore]):
    model = WorkLogScore

    async def get_by_run_and_email(
        self, run_id: int, email: str
    ) -> WorkLogScore | None:
        result = await self._session.execute(
            select(WorkLogScore).where(
                WorkLogScore.evaluation_run_id == run_id,
                WorkLogScore.employee_email == email,
            )
        )
        return result.scalar_one_or_none()


class TLAssessmentRepository(BaseRepository[TLAssessmentScore]):
    model = TLAssessmentScore

    async def get_by_run_and_email(
        self, run_id: int, email: str
    ) -> TLAssessmentScore | None:
        result = await self._session.execute(
            select(TLAssessmentScore).where(
                TLAssessmentScore.evaluation_run_id == run_id,
                TLAssessmentScore.employee_email == email,
            )
        )
        return result.scalar_one_or_none()

    async def exists_for_run_and_email(self, run_id: int, email: str) -> bool:
        result = await self._session.execute(
            select(TLAssessmentScore.id).where(
                TLAssessmentScore.evaluation_run_id == run_id,
                TLAssessmentScore.employee_email == email,
            )
        )
        return result.scalar_one_or_none() is not None

    async def get_for_employee_period(
        self,
        employee_email: str,
        year: int,
        month: int,
        evaluation_run_id: int | None = None,
    ) -> TLAssessmentScore | None:
        """Fetch TL assessment for an employee for a specific year/month.

        Pass ``evaluation_run_id`` to scope to a single run (avoids
        MultipleResultsFound when the same employee/period was processed
        across several runs).
        """
        filters = [
            TLAssessmentScore.employee_email == employee_email,
            TLAssessmentScore.year == year,
            TLAssessmentScore.month == month,
        ]
        if evaluation_run_id is not None:
            filters.append(TLAssessmentScore.evaluation_run_id == evaluation_run_id)
        result = await self._session.execute(select(TLAssessmentScore).where(*filters))
        # Use .first() so stale duplicate rows from earlier runs never crash us
        return result.scalars().first()


class FinalScoreRepository(BaseRepository[FinalScore]):
    model = FinalScore

    async def get_by_email_and_period(
        self, email: str, year: int, month: int
    ) -> FinalScore | None:
        result = await self._session.execute(
            select(FinalScore).where(
                FinalScore.employee_email == email,
                FinalScore.year == year,
                FinalScore.month == month,
            )
        )
        return result.scalar_one_or_none()

    async def get_team_scores_by_period(
        self, emails: list[str], year: int, month: int
    ) -> list[FinalScore]:
        result = await self._session.execute(
            select(FinalScore).where(
                FinalScore.employee_email.in_(emails),
                FinalScore.year == year,
                FinalScore.month == month,
            )
        )
        return list(result.scalars().all())

    async def get_team_scores(self, run_id: int, emails: list[str]) -> list[FinalScore]:
        result = await self._session.execute(
            select(FinalScore).where(
                FinalScore.evaluation_run_id == run_id,
                FinalScore.employee_email.in_(emails),
            )
        )
        return list(result.scalars().all())

    async def get_history_for_reward(
        self, email: str, year: int, month: int, months_back: int = 3
    ) -> list[FinalScore]:
        """
        Fetch recent FinalScore rows for the reward calculation.
        The reward query uses AVG over recent evaluations from scoring_summary.
        We approximate this by fetching the last N months.
        """
        result = await self._session.execute(
            select(FinalScore)
            .where(FinalScore.employee_email == email)
            .order_by(FinalScore.year.desc(), FinalScore.month.desc())
            .limit(months_back)
        )
        return list(result.scalars().all())


class DeveloperFinalScoreRepository(BaseRepository[DeveloperFinalScore]):
    model = DeveloperFinalScore

    async def get_by_run_id(
        self, run_id: int, emails: list[str]
    ) -> list[DeveloperFinalScore]:
        result = await self._session.execute(
            select(DeveloperFinalScore).where(
                DeveloperFinalScore.evaluation_run_id == run_id,
                DeveloperFinalScore.employee_email.in_(emails),
            )
        )
        return list(result.scalars().all())

    async def get_by_employee_period(
        self, employee_id: str, year: int, month: int
    ) -> DeveloperFinalScore | None:
        result = await self._session.execute(
            select(DeveloperFinalScore).where(
                DeveloperFinalScore.employee_id == employee_id,
                DeveloperFinalScore.year == year,
                DeveloperFinalScore.month == month,
            )
        )
        return result.scalars().first()
