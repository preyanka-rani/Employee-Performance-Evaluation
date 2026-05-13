"""
tests/integration/test_api.py
──────────────────────────────
Integration tests for FastAPI endpoints using in-memory SQLite.
Tests cover: /health, /evaluations, /upload/tl-scores, /reports.
"""

import io
import pytest
from httpx import AsyncClient


class TestHealthEndpoint:
    @pytest.mark.asyncio
    async def test_health_returns_ok(self, client: AsyncClient):
        response = await client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert "version" in data


class TestEvaluationsEndpoint:
    @pytest.mark.asyncio
    async def test_run_evaluation_without_auth_returns_401(self, engine):
        """Unauthenticated request must be rejected."""
        from httpx import ASGITransport, AsyncClient
        from app.main import app

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            response = await ac.post(
                "/api/v1/evaluations/run",
                json={"team": "developer", "year": 2024, "month": 12},
            )
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_run_evaluation_authenticated(self, client: AsyncClient):
        """Authenticated request triggers evaluation run and returns run ID."""
        response = await client.post(
            "/api/v1/evaluations/run",
            json={"team": "developer", "year": 2024, "month": 11},
        )
        # Celery task will be queued; we expect 202 or 201
        assert response.status_code in (201, 202)
        data = response.json()
        assert "run_id" in data or "id" in data

    @pytest.mark.asyncio
    async def test_get_evaluation_not_found(self, client: AsyncClient):
        """Requesting a non-existent evaluation returns 404."""
        response = await client.get("/api/v1/evaluations/99999")
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_get_existing_evaluation(
        self, client: AsyncClient, test_evaluation_run
    ):
        """Requesting an existing evaluation returns 200 with status info."""
        run_id = test_evaluation_run.id
        response = await client.get(f"/api/v1/evaluations/{run_id}")
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == run_id
        assert "status" in data

    @pytest.mark.asyncio
    async def test_duplicate_run_returns_409(self, client: AsyncClient):
        """Two requests for same team/period → second is 409 conflict."""
        payload = {"team": "developer", "year": 2024, "month": 10}
        first = await client.post("/api/v1/evaluations/run", json=payload)
        assert first.status_code in (201, 202)

        second = await client.post("/api/v1/evaluations/run", json=payload)
        assert second.status_code == 409


class TestUploadTLScores:
    def _make_excel_bytes(self) -> bytes:
        """Generate a minimal valid TL assessment Excel file in memory."""
        import openpyxl

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(["email", "problem_solving", "kpi", "general"])
        ws.append(["alice@example.com", 8, 12, 10])
        buf = io.BytesIO()
        wb.save(buf)
        return buf.getvalue()

    @pytest.mark.asyncio
    async def test_upload_valid_excel(self, client: AsyncClient, test_evaluation_run):
        excel_bytes = self._make_excel_bytes()
        response = await client.post(
            "/api/v1/upload/tl-scores",
            files={
                "file": (
                    "tl_scores.xlsx",
                    excel_bytes,
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            },
            data={"evaluation_run_id": str(test_evaluation_run.id)},
        )
        assert response.status_code == 200
        data = response.json()
        assert "rows_saved" in data

    @pytest.mark.asyncio
    async def test_upload_invalid_file_extension(
        self, client: AsyncClient, test_evaluation_run
    ):
        response = await client.post(
            "/api/v1/upload/tl-scores",
            files={"file": ("scores.txt", b"not an excel file", "text/plain")},
            data={"evaluation_run_id": str(test_evaluation_run.id)},
        )
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_upload_nonexistent_run_returns_404(self, client: AsyncClient):
        excel_bytes = self._make_excel_bytes()
        response = await client.post(
            "/api/v1/upload/tl-scores",
            files={
                "file": (
                    "tl_scores.xlsx",
                    excel_bytes,
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            },
            data={"evaluation_run_id": "99999"},
        )
        assert response.status_code == 404


class TestReportsEndpoint:
    @pytest.mark.asyncio
    async def test_employee_report_not_found(self, client: AsyncClient):
        response = await client.get("/api/v1/reports/NONEXISTENT/2024/12")
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_team_report_empty(self, client: AsyncClient):
        response = await client.get("/api/v1/reports/team/developer/2024/1")
        # Returns 200 with empty list (no evaluated employees yet)
        assert response.status_code in (200, 404)

    @pytest.mark.asyncio
    async def test_employee_report_exists(
        self, client: AsyncClient, test_employee, db_session
    ):
        """Seed a FinalScore and verify report endpoint returns it."""
        from datetime import datetime, timezone
        from app.models.scores import FinalScore

        score = FinalScore(
            evaluation_run_id=1,
            employee_email=test_employee.email,
            quality_check_score=80.0,
            work_log_score=90.0,
            sentiment_score=60.0,
            attendance_score=75.0,
            problem_solving_score=8.0,
            kpi_score=12.0,
            general_score=10.0,
            segment_a_marks=41.5,
            segment_b_marks=37.5,
            base_total=79.0,
            reward_score=4.5,
            final_score=79.5,
            year=2024,
            month=12,
            created_at=datetime.now(timezone.utc),
        )
        db_session.add(score)
        await db_session.commit()

        response = await client.get(
            f"/api/v1/reports/{test_employee.employee_id}/2024/12"
        )
        assert response.status_code == 200
        data = response.json()
        assert data["employee_id"] == test_employee.employee_id
        assert "final_score" in data
