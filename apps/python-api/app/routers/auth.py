"""Authentication routes: register, login, refresh, whoami."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import User, UserRole
from app.schemas import (
    TokenOut,
    UserLoginIn,
    UserOut,
    UserRegisterIn,
)
from app.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    get_current_user,
    hash_password,
    verify_password,
)
from app.config import settings

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _tokens_for(user: User) -> TokenOut:
    return TokenOut(
        access_token=create_access_token(str(user.id), role=user.role.value),
        refresh_token=create_refresh_token(str(user.id)),
        expires_in=settings.access_token_ttl_minutes * 60,
    )


@router.post("/register", response_model=TokenOut, status_code=status.HTTP_201_CREATED)
def register(payload: UserRegisterIn, db: Session = Depends(get_db)) -> TokenOut:
    """Create a new account and return tokens.

    The first account created on a fresh deployment is promoted to `owner`
    so the platform can be bootstrapped without a manual DB edit.
    """
    email = payload.email.lower().strip()
    existing = db.scalar(select(User).where(User.email == email))
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Email already registered"
        )

    is_first_user = db.scalar(select(User.id).limit(1)) is None
    user = User(
        email=email,
        password_hash=hash_password(payload.password),
        display_name=payload.display_name.strip(),
        role=UserRole.owner if is_first_user else UserRole.user,
    )
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Email already registered"
        ) from None
    db.refresh(user)
    return _tokens_for(user)


@router.post("/login", response_model=TokenOut)
def login(payload: UserLoginIn, db: Session = Depends(get_db)) -> TokenOut:
    """Exchange credentials for an access + refresh token pair."""
    email = payload.email.lower().strip()
    user = db.scalar(select(User).where(User.email == email))
    # Verify against a dummy hash when the user is missing to keep the
    # response time constant and avoid user enumeration.
    if user is None:
        verify_password(
            payload.password,
            "pbkdf2_sha256$600000$AAAAAAAAAAAAAAAAAAAAAA==$AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials"
        )
    if not verify_password(payload.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials"
        )
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Account is disabled"
        )
    return _tokens_for(user)


@router.post("/refresh", response_model=TokenOut)
def refresh(token: str, db: Session = Depends(get_db)) -> TokenOut:
    """Exchange a refresh token for a new token pair."""
    payload = decode_token(token, expected_type="refresh")
    try:
        user_id = int(payload.get("sub", ""))
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token"
        ) from None
    user = db.get(User, user_id)
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token"
        )
    return _tokens_for(user)


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)) -> User:
    """Return the authenticated user's profile."""
    return user
