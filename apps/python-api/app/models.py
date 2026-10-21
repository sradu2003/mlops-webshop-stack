"""ORM models for the webshop / co-creation platform.

Domain model
------------
User          registered account, role-gated.
Content       a piece of multi-modal content (tutorial/course/video) owned by
              an author, with a revision history and a publication state.
Revision      immutable snapshot of a Content item (co-authoring history).
Contribution  a co-author's contribution to a Content item.
Asset         a stored multi-modal asset (video, image, audio, doc) belonging
              to a Content item, with a storage key and processing state.
Entitlement   a user's right to consume a piece of content (purchase, grant).
UsageEvent    an immutable analytics event (view, play, download, completion).
              This is the raw material for monetisation and abuse detection.
AnonymousId   a privacy-preserving pseudonymous identifier used to attribute
              usage from unregistered visitors.
AuditLog      record of sensitive administrative actions.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utcnow,
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utcnow,
        onupdate=_utcnow,
        server_default=func.now(),
        nullable=False,
    )


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class UserRole(str, enum.Enum):
    anonymous = "anonymous"
    user = "user"
    author = "author"
    curator = "curator"
    admin = "admin"
    owner = "owner"


class ContentStatus(str, enum.Enum):
    draft = "draft"
    in_review = "in_review"
    published = "published"
    archived = "archived"


class ContentType(str, enum.Enum):
    tutorial = "tutorial"
    course = "course"
    video = "video"
    article = "article"
    live_session = "live_session"


class AssetKind(str, enum.Enum):
    video = "video"
    audio = "audio"
    image = "image"
    document = "document"
    subtitle = "subtitle"
    archive = "archive"


class AssetStatus(str, enum.Enum):
    uploading = "uploading"
    processing = "processing"
    ready = "ready"
    failed = "failed"
    quarantined = "quarantined"


class UsageEventType(str, enum.Enum):
    page_view = "page_view"
    asset_impression = "asset_impression"
    play = "play"
    watch_progress = "watch_progress"
    completion = "completion"
    download = "download"
    search = "search"
    chat_message = "chat_message"
    share = "share"
    purchase = "purchase"


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    display_name: Mapped[str] = mapped_column(String(120))
    role: Mapped[UserRole] = mapped_column(
        Enum(UserRole, name="user_role", values_callable=lambda e: [m.value for m in e]),
        default=UserRole.user,
        server_default=UserRole.user.value,
        index=True,
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    email_verified: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )

    # Monetisation split: percentage of net revenue credited to the author.
    revenue_share_pct: Mapped[float] = mapped_column(
        Float, default=70.0, server_default="70.0"
    )
    balance_cents: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0"
    )
    stripe_account_id: Mapped[str | None] = mapped_column(String(120), nullable=True)

    contents: Mapped[list["Content"]] = relationship(
        back_populates="author", cascade="all, delete-orphan"
    )
    entitlements: Mapped[list["Entitlement"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"<User id={self.id} email={self.email!r} role={self.role}>"


class AnonymousId(Base, TimestampMixin):
    """Pseudonymous identifier for unregistered visitors.

    A salted HMAC of the IP + user-agent is stored, never the raw identifiers.
    Rotation is supported by `salt_version` so historical events remain
    attributable within a season but can be purged on request.
    """

    __tablename__ = "anonymous_ids"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    token: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    salt_version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    first_seen: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now()
    )
    last_seen: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now()
    )
    session_count: Mapped[int] = mapped_column(Integer, default=1, server_default="1")

    __table_args__ = (Index("ix_anonymous_ids_last_seen", "last_seen"),)


# ---------------------------------------------------------------------------
# Content & collaboration
# ---------------------------------------------------------------------------


class Content(Base, TimestampMixin):
    __tablename__ = "contents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    uuid: Mapped[str] = mapped_column(
        String(36), default=lambda: str(uuid.uuid4()), unique=True, index=True
    )
    title: Mapped[str] = mapped_column(String(300))
    slug: Mapped[str] = mapped_column(String(300), unique=True, index=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_type: Mapped[ContentType] = mapped_column(
        Enum(ContentType, name="content_type", values_callable=lambda e: [m.value for m in e]),
        default=ContentType.tutorial,
        server_default=ContentType.tutorial.value,
    )
    status: Mapped[ContentStatus] = mapped_column(
        Enum(ContentStatus, name="content_status", values_callable=lambda e: [m.value for m in e]),
        default=ContentStatus.draft,
        server_default=ContentStatus.draft.value,
        index=True,
    )

    author_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    price_cents: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    currency: Mapped[str] = mapped_column(String(3), default="EUR", server_default="EUR")
    is_listed: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    duration_seconds: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0"
    )
    tags: Mapped[list] = mapped_column(JSONB, default=list, server_default="[]")

    author: Mapped[User] = relationship(back_populates="contents")
    assets: Mapped[list["Asset"]] = relationship(
        back_populates="content", cascade="all, delete-orphan"
    )
    contributions: Mapped[list["Contribution"]] = relationship(
        back_populates="content", cascade="all, delete-orphan"
    )
    revisions: Mapped[list["Revision"]] = relationship(
        back_populates="content", cascade="all, delete-orphan"
    )
    entitlements: Mapped[list["Entitlement"]] = relationship(
        back_populates="content", cascade="all, delete-orphan"
    )


class Contribution(Base, TimestampMixin):
    """A co-author's contribution to a content item."""

    __tablename__ = "contributions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    content_id: Mapped[int] = mapped_column(
        ForeignKey("contents.id", ondelete="CASCADE"), index=True
    )
    contributor_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(50), default="co_author")
    revenue_share_pct: Mapped[float] = mapped_column(
        Float, default=10.0, server_default="10.0"
    )
    is_accepted: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true"
    )

    content: Mapped[Content] = relationship(back_populates="contributions")
    contributor: Mapped[User] = relationship()


class Revision(Base, TimestampMixin):
    """Immutable snapshot for the co-authoring history / audit trail."""

    __tablename__ = "revisions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    content_id: Mapped[int] = mapped_column(
        ForeignKey("contents.id", ondelete="CASCADE"), index=True
    )
    author_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    revision_number: Mapped[int] = mapped_column(Integer, default=1)
    title: Mapped[str] = mapped_column(String(300))
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    change_note: Mapped[str | None] = mapped_column(String(500), nullable=True)

    content: Mapped[Content] = relationship(back_populates="revisions")
    __table_args__ = (
        UniqueConstraint("content_id", "revision_number", name="uq_revision_number"),
    )


# ---------------------------------------------------------------------------
# Assets
# ---------------------------------------------------------------------------


class Asset(Base, TimestampMixin):
    """A stored multi-modal asset (video, audio, image, document).

    `storage_key` points at the object-store key; bytes are never stored in
    the database. `sha256` gives content-addressed de-duplication.
    """

    __tablename__ = "assets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    uuid: Mapped[str] = mapped_column(
        String(36), default=lambda: str(uuid.uuid4()), unique=True, index=True
    )
    content_id: Mapped[int] = mapped_column(
        ForeignKey("contents.id", ondelete="CASCADE"), index=True
    )
    uploaded_by_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[AssetKind] = mapped_column(
        Enum(AssetKind, name="asset_kind", values_callable=lambda e: [m.value for m in e]),
        default=AssetKind.video,
        server_default=AssetKind.video.value,
    )
    status: Mapped[AssetStatus] = mapped_column(
        Enum(AssetStatus, name="asset_status", values_callable=lambda e: [m.value for m in e]),
        default=AssetStatus.uploading,
        server_default=AssetStatus.uploading.value,
        index=True,
    )
    filename: Mapped[str] = mapped_column(String(400))
    storage_key: Mapped[str] = mapped_column(String(600), unique=True)
    mime_type: Mapped[str] = mapped_column(String(150), default="application/octet-stream")
    size_bytes: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    sha256: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    duration_seconds: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    poster_key: Mapped[str | None] = mapped_column(String(600), nullable=True)
    transcript_key: Mapped[str | None] = mapped_column(String(600), nullable=True)
    is_previewable: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )

    content: Mapped[Content] = relationship(back_populates="assets")


# ---------------------------------------------------------------------------
# Commercialisation
# ---------------------------------------------------------------------------


class Entitlement(Base, TimestampMixin):
    """A user's right to consume a content item."""

    __tablename__ = "entitlements"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    content_id: Mapped[int] = mapped_column(
        ForeignKey("contents.id", ondelete="CASCADE"), index=True
    )
    source: Mapped[str] = mapped_column(String(50), default="purchase")
    amount_cents: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    currency: Mapped[str] = mapped_column(String(3), default="EUR", server_default="EUR")
    granted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now()
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    revoked: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")

    user: Mapped[User] = relationship(back_populates="entitlements")
    content: Mapped[Content] = relationship(back_populates="entitlements")

    __table_args__ = (
        UniqueConstraint("user_id", "content_id", name="uq_user_content_entitlement"),
    )


class UsageEvent(Base):
    """Immutable analytics event.

    This is the high-volume table. It is append-only and indexed for the
    aggregate queries the monetisation dashboard runs. Consider a monthly
    partition strategy once the row count outgrows the index cache.
    """

    __tablename__ = "usage_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now(), index=True
    )
    event_type: Mapped[UsageEventType] = mapped_column(
        Enum(UsageEventType, name="usage_event_type", values_callable=lambda e: [m.value for m in e]),
        index=True,
    )
    content_id: Mapped[int | None] = mapped_column(
        ForeignKey("contents.id", ondelete="SET NULL"), nullable=True, index=True
    )
    asset_id: Mapped[int | None] = mapped_column(
        ForeignKey("assets.id", ondelete="SET NULL"), nullable=True
    )
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    anonymous_id: Mapped[int | None] = mapped_column(
        ForeignKey("anonymous_ids.id", ondelete="SET NULL"), nullable=True, index=True
    )
    session_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    watch_seconds: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    completion_ratio: Mapped[float] = mapped_column(
        Float, default=0.0, server_default="0.0"
    )
    revenue_cents: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    referrer: Mapped[str | None] = mapped_column(String(400), nullable=True)
    device: Mapped[str | None] = mapped_column(String(80), nullable=True)
    country: Mapped[str | None] = mapped_column(String(8), nullable=True)
    extra: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")

    __table_args__ = (
        Index("ix_usage_events_content_time", "content_id", "occurred_at"),
        Index("ix_usage_events_type_time", "event_type", "occurred_at"),
    )


class AuditLog(Base, TimestampMixin):
    """Record of sensitive administrative actions."""

    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    actor_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    action: Mapped[str] = mapped_column(String(120), index=True)
    target_type: Mapped[str | None] = mapped_column(String(60), nullable=True)
    target_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    detail: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
