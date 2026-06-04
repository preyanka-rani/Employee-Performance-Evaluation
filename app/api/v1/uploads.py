"""
app/api/v1/uploads.py
──────────────────────
Excel upload endpoint for Team Lead assessment scores.

POST /api/v1/upload/tl-scores
    Accepts: multipart/form-data with an Excel file (.xlsx or .xls)
    Validates: column names, score ranges
    Persists: TLAssessmentScore rows; skips duplicates per employee/period
"""

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.models.scores import TLAssessmentScore
from app.repositories.evaluation_repository import EvaluationRepository
from app.repositories.score_repository import TLAssessmentRepository
from app.schemas.scores import TLAssessmentUploadResponse
from app.shared.excel_parser.parser import ExcelParseError
from app.shared.excel_parser.tl_assessment import parse_tl_assessment_excel

router = APIRouter(prefix="/upload", tags=["uploads"])

MAX_FILE_SIZE_BYTES = 5 * 1024 * 1024  # 5 MB


@router.post(
    "/tl-scores",
    response_model=TLAssessmentUploadResponse,
    status_code=status.HTTP_200_OK,
)
async def upload_tl_scores(
    file: UploadFile = File(...),
    evaluation_run_id: int = Form(...),
    year: int = Form(...),
    month: int = Form(...),
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
) -> TLAssessmentUploadResponse:
    """
    Upload a TL Assessment Excel file for a given evaluation run + period.

    Duplicate entries (same email, year, month) are skipped.
    Returns counts of inserted vs skipped rows along with any parse errors.
    """
    # Validate evaluation run exists
    eval_repo = EvaluationRepository(db)
    run = await eval_repo.get_by_id(evaluation_run_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Evaluation run {evaluation_run_id} not found.",
        )

    # Read and size-limit file
    file_bytes = await file.read()
    if len(file_bytes) > MAX_FILE_SIZE_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="File exceeds 5 MB limit.",
        )

    # Parse Excel
    try:
        parse_result = parse_tl_assessment_excel(file_bytes)
    except ExcelParseError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        )

    # Persist rows
    tl_repo = TLAssessmentRepository(db)
    inserted = 0
    skipped = 0

    for row in parse_result.rows:
        exists = await tl_repo.get_for_employee_period(
            employee_email=row.employee_email,
            year=year,
            month=month,
        )
        if exists:
            skipped += 1
            continue

        await tl_repo.create(
            TLAssessmentScore(
                evaluation_run_id=evaluation_run_id,
                employee_email=row.employee_email,
                year=year,
                month=month,
                problem_solving=row.problem_solving,
                kpi=row.kpi,
                general=row.general,
                total=row.problem_solving + row.kpi + row.general,
                uploaded_by=current_user.get("sub", "api"),
            )
        )
        inserted += 1

    await db.commit()

    return TLAssessmentUploadResponse(
        inserted=inserted,
        skipped=skipped,
        errors=parse_result.errors,
    )
