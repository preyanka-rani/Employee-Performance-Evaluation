"""
app/core/security.py
─────────────────────
API authentication helpers using JWT Bearer tokens.
Passwords are hashed with bcrypt. Tokens are signed with HS256.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

from jose import JWTError, jwt
from passlib.context import CryptContext

from app.core.config import get_settings

settings = get_settings()

_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

ALGORITHM = "HS256"


def hash_password(plain: str) -> str:
    return _pwd_context.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    return _pwd_context.verify(plain, hashed)


def create_access_token(
    subject: str | Any, expires_delta: timedelta | None = None
) -> str:
    """
    Create a signed JWT.

    Args:
        subject: The identity payload (e.g. user email / id).
        expires_delta: Custom expiry; defaults to settings.api_token_expire_minutes.
    """
    expire = datetime.now(UTC) + (
        expires_delta or timedelta(minutes=settings.api_token_expire_minutes)
    )
    payload = {"sub": str(subject), "exp": expire, "iat": datetime.now(UTC)}
    return jwt.encode(payload, settings.api_secret_key, algorithm=ALGORITHM)


def decode_access_token(token: str) -> dict[str, Any] | None:
    """
    Decode and verify a JWT. Returns None on any failure.
    """
    try:
        return jwt.decode(token, settings.api_secret_key, algorithms=[ALGORITHM])
    except JWTError:
        return None
