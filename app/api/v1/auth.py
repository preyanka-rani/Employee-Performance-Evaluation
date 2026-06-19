"""
app/api/v1/auth.py
──────────────────
Authentication endpoints.

POST /api/v1/auth/token
    Accepts username + password (form data).
    Returns a signed JWT Bearer token.

Usage in Swagger UI:
  1. Expand the POST /api/v1/auth/token endpoint.
  2. Fill in username/password and click Execute.
  3. Copy the access_token value from the response.
  4. Click the padlock icon (🔒) at the top of Swagger.
  5. Paste the token and click Authorize.
  6. All protected endpoints now work.
"""

from fastapi import APIRouter, Form, HTTPException, status

from app.core.config import get_settings
from app.core.security import create_access_token

router = APIRouter(prefix="/auth", tags=["auth"])
settings = get_settings()


@router.post(
    "/token",
    summary="Get Bearer token",
    description=(
        "Exchange admin username + password for a JWT Bearer token. "
        "Copy the returned `access_token` and paste it into the Swagger "
        "🔒 Authorize dialog to unlock all protected endpoints."
    ),
    response_model=dict,
)
async def login_for_access_token(
    username: str = Form(..., examples=["admin"]),
    password: str = Form(..., examples=["admin123"]),
) -> dict:
    """Return a JWT if credentials match the configured admin account."""
    if username != settings.admin_username or password != settings.admin_password:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = create_access_token(subject=username)
    return {
        "access_token": token,
        "token_type": "bearer",
        "expires_in_minutes": settings.api_token_expire_minutes,
    }
