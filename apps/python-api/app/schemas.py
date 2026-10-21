"""Pydantic request/response schemas with strict validation.

Validation rules that matter commercially:
  * prices are integer MINOR UNITS (cents), never floats -> no rounding drift
  * `price_cents` cannot be negative
  * content cannot be published without a title/slug
  * passwords have a minimum strength
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Annotated, Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

T = TypeVar("T")

SlugStr = Annotated[
    str, Field(min_length=3, max_length=300, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
]
PasswordStr = Annotated[str, Field(min_length=8, max_length=128)]


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int
    page_size: int

    @property
    def pages(self) -> int:
        if self.page_size <= 0:
            return 0
        return (self.total + self.page_size - 1) // self.page_size


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


class UserRegisterIn(BaseModel):
    email: EmailStr
    password: PasswordStr
    display_name: str = Field(min_length=2, max_length=120)

    @field_validator("password")
    @classmethod
    def _password_strength(cls, v: str) -> str:
        if not re.search(r"[A-Za-z]", v) or not re.search(r"\d", v):
            raise ValueError("password must contain at least one letter and one digit")
        return v

    @field_validator("display_name")
    @classmethod
    def _strip_name(cls, v: str) -> str:
        return v.strip()


class UserLoginIn(BaseModel):
    email: EmailStr
    password: str


class UserOut(ORMModel):
    id: int
    email: EmailStr
    display_name: str
    role: str
    is_active: bool
    email_verified: bool
    revenue_share_pct: float
    created_at: datetime


class TokenOut(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int


# ---------------------------------------------------------------------------
# Content
# ---------------------------------------------------------------------------


class ContentIn(BaseModel):
    title: str = Field(min_length=3, max_length=300)
    slug: SlugStr
    summary: str | None = Field(default=None, max_length=2000)
    body: str | None = None
    content_type: str = "tutorial"
    price_cents: int = Field(default=0, ge=0, le=100_000_000)
    currency: str = Field(default="EUR", min_length=3, max_length=3)
    tags: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("currency")
    @classmethod
    def _upper_currency(cls, v: str) -> str:
        return v.upper()

    @field_validator("title")
    @classmethod
    def _strip_title(cls, v: str) -> str:
        v = v.strip()
        if len(v) < 3:
            raise ValueError("title must be at least 3 characters")
        return v


class ContentUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=3, max_length=300)
    summary: str | None = Field(default=None, max_length=2000)
    body: str | None = None
    content_type: str | None = None
    price_cents: int | None = Field(default=None, ge=0, le=100_000_000)
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    status: str | None = None
    is_listed: bool | None = None
    tags: list[str] | None = Field(default=None, max_length=20)


class ContentOut(ORMModel):
    id: int
    uuid: str
    title: str
    slug: str
    summary: str | None
    content_type: str
    status: str
    author_id: int
    price_cents: int
    currency: str
    is_listed: bool
    published_at: datetime | None
    duration_seconds: int
    tags: list[str]
    created_at: datetime
    updated_at: datetime


class ContributionIn(BaseModel):
    contributor_id: int
    role: str = Field(default="co_author", max_length=50)
    revenue_share_pct: float = Field(default=10.0, ge=0.0, le=100.0)


class ContributionOut(ORMModel):
    id: int
    content_id: int
    contributor_id: int
    role: str
    revenue_share_pct: float
    is_accepted: bool
    created_at: datetime


# ---------------------------------------------------------------------------
# Assets
# ---------------------------------------------------------------------------


class AssetIn(BaseModel):
    content_id: int
    filename: str = Field(min_length=1, max_length=400)
    kind: str = "video"
    mime_type: str = Field(default="application/octet-stream", max_length=150)
    size_bytes: int = Field(default=0, ge=0)
    sha256: str | None = Field(default=None, pattern=r"^[a-fA-F0-9]{64}$")


class AssetOut(ORMModel):
    id: int
    uuid: str
    content_id: int
    uploaded_by_id: int
    kind: str
    status: str
    filename: str
    storage_key: str
    mime_type: str
    size_bytes: int
    duration_seconds: int
    width: int | None
    height: int | None
    is_previewable: bool
    created_at: datetime


# ---------------------------------------------------------------------------
# Usage / analytics
# ---------------------------------------------------------------------------


class UsageEventIn(BaseModel):
    event_type: str
    content_id: int | None = None
    asset_id: int | None = None
    session_id: str | None = Field(default=None, max_length=64)
    watch_seconds: int = Field(default=0, ge=0)
    completion_ratio: float = Field(default=0.0, ge=0.0, le=1.0)
    referrer: str | None = Field(default=None, max_length=400)
    device: str | None = Field(default=None, max_length=80)
    country: str | None = Field(default=None, max_length=8)
    extra: dict[str, Any] = Field(default_factory=dict)

    @field_validator("completion_ratio")
    @classmethod
    def _clamp_ratio(cls, v: float) -> float:
        return min(max(v, 0.0), 1.0)


class UsageEventOut(ORMModel):
    id: int
    occurred_at: datetime
    event_type: str
    content_id: int | None
    asset_id: int | None
    user_id: int | None
    watch_seconds: int
    completion_ratio: float


# ---------------------------------------------------------------------------
# Commerce
# ---------------------------------------------------------------------------


class EntitlementOut(ORMModel):
    id: int
    user_id: int
    content_id: int
    source: str
    amount_cents: int
    currency: str
    granted_at: datetime
    expires_at: datetime | None
    revoked: bool


class CheckoutIn(BaseModel):
    content_id: int
    success_url: str | None = None
    cancel_url: str | None = None


class CheckoutOut(BaseModel):
    checkout_url: str
    session_id: str
    amount_cents: int
    currency: str
