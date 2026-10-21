"""Authentication & authorisation primitives.

Password hashing uses PBKDF2-HMAC-SHA256 from the standard library so the
service has no native build dependency and no `bcrypt`/`argon2` binary
requirement. Passlib is not used: it is unmaintained and pulls in a native
backend.

JWTs are signed with HS256 using `settings.jwt_secret`.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.models import User

# --- Password hashing (PBKDF2-HMAC-SHA256) ----------------------------------

PBKDF2_ALGORITHM = "pbkdf2_sha256"
PBKDF2_ITERATIONS = 600_000
SALT_BYTES = 16


def hash_password(password: str) -> str:
    """Return an encoded password: pbkdf2_sha256$iterations$salt$hash."""
    if not password or len(password) < 8:
        raise ValueError("password must be at least 8 characters")
    salt = os.urandom(SALT_BYTES)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS
    )
    return "$".join(
        [
            PBKDF2_ALGORITHM,
            str(PBKDF2_ITERATIONS),
            base64.b64encode(salt).decode("ascii"),
            base64.b64encode(digest).decode("ascii"),
        ]
    )


def verify_password(password: str, encoded: str) -> bool:
    """Constant-time verification of a password against an encoded hash."""
    try:
        algorithm, iterations, salt_b64, hash_b64 = encoded.split("$")
        if algorithm != PBKDF2_ALGORITHM:
            return False
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(hash_b64)
        actual = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), salt, int(iterations)
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


# --- Tokens -----------------------------------------------------------------


def create_access_token(
    subject: str, *, role: str = "user", expires_delta: timedelta | None = None
) -> str:
    now = datetime.now(timezone.utc)
    expire = now + (
        expires_delta or timedelta(minutes=settings.access_token_ttl_minutes)
    )
    payload: dict[str, Any] = {
        "sub": subject,
        "role": role,
        "iat": int(now.timestamp()),
        "exp": int(expire.timestamp()),
        "jti": secrets.token_urlsafe(16),
        "typ": "access",
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def create_refresh_token(subject: str) -> str:
    now = datetime.now(timezone.utc)
    expire = now + timedelta(days=settings.refresh_token_ttl_days)
    payload = {
        "sub": subject,
        "iat": int(now.timestamp()),
        "exp": int(expire.timestamp()),
        "jti": secrets.token_urlsafe(16),
        "typ": "refresh",
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_token(token: str, *, expected_type: str = "access") -> dict[str, Any]:
    try:
        payload = jwt.decode(
            token, settings.jwt_secret, algorithms=[settings.jwt_algorithm]
        )
    except JWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    if payload.get("typ") != expected_type:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token type",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return payload


# --- FastAPI dependencies ---------------------------------------------------

bearer_scheme = HTTPBearer(auto_error=False)

CREDENTIALS_ERROR = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Not authenticated",
    headers={"WWW-Authenticate": "Bearer"},
)


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> User:
    """Resolve the bearer token to an active user, or raise 401."""
    if credentials is None or not credentials.credentials:
        raise CREDENTIALS_ERROR

    payload = decode_token(credentials.credentials, expected_type="access")
    subject = payload.get("sub")
    if not subject:
        raise CREDENTIALS_ERROR

    try:
        user_id = int(subject)
    except (TypeError, ValueError) as exc:
        raise CREDENTIALS_ERROR from exc

    user = db.scalar(select(User).where(User.id == user_id))
    if user is None:
        raise CREDENTIALS_ERROR
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Account is disabled"
        )
    return user


def get_current_user_optional(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> User | None:
    """Resolve the bearer token to a user if one is present and valid.

    Unlike `get_current_user` this never raises for a missing or invalid
    token — it returns None. It is used by endpoints that are PUBLIC but
    show more to a signed-in caller (for example, a draft that only its
    author can see).

    An invalid token is treated as "not signed in" rather than an error,
    so a stale token in a browser does not turn a public page into a 401.
    """
    if credentials is None or not credentials.credentials:
        return None
    try:
        payload = decode_token(credentials.credentials, expected_type="access")
    except HTTPException:
        return None
    subject = payload.get("sub")
    if not subject:
        return None
    try:
        user_id = int(subject)
    except (TypeError, ValueError):
        return None
    user = db.scalar(select(User).where(User.id == user_id))
    if user is None or not user.is_active:
        return None
    return user


def require_admin(user: User = Depends(get_current_user)) -> User:
    """Dependency for admin-only endpoints."""
    if user.role not in {"admin", "owner"}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Admin privileges required"
        )
    return user


def require_role(*roles: str):
    """Dependency factory: require the caller to hold one of ``roles``."""

    def _checker(user: User = Depends(get_current_user)) -> User:
        if user.role not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Requires one of: {', '.join(sorted(roles))}",
            )
        return user

    return _checker
