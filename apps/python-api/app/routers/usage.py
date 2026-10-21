"""Usage telemetry routes.

Design notes
------------
* Both registered and anonymous visitors are recorded in the same
  `usage_events` table so the monetisation funnel can be computed over the
  whole audience.
* Anonymous visitors are identified by a salted HMAC token derived from
  IP + User-Agent + a rotating salt. The raw IP/UA are never persisted.
* `X-Forwarded-For` is only trusted when the request arrives from the
  nginx reverse proxy (127.0.0.1), otherwise a client could spoof it.
"""

from __future__ import annotations

import hashlib
import hmac
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import (
    AnonymousId,
    Content,
    UsageEvent,
    UsageEventType,
    User,
)
from app.schemas import Page, UsageEventIn, UsageEventOut
from app.security import require_role
from app.config import settings

router = APIRouter(prefix="/api/usage", tags=["usage"])

# Roles permitted to read the RAW event stream. The aggregate /summary
# endpoint stays open because it exposes no identities.
ANALYTICS_ROLES = {"curator", "admin", "owner"}

# Salt used to pseudonymise anonymous visitors. Read from the validated
# settings so a placeholder can never reach the HMAC. Rotate to invalidate.
_ANON_SALT = settings.anon_salt.encode()


def _client_ip(request: Request, xff: str | None) -> str:
    """Resolve the client IP, trusting XFF only from the local proxy."""
    client = request.client
    if client and client.host in {"127.0.0.1", "::1"} and xff:
        return xff.split(",")[0].strip()
    return client.host if client else "0.0.0.0"


def _anon_token(ip: str, user_agent: str) -> str:
    raw = f"{ip}|{user_agent}".encode()
    return hmac.new(_ANON_SALT, raw, hashlib.sha256).hexdigest()


def _resolve_anonymous_id(
    db: Session, ip: str, user_agent: str
) -> AnonymousId | None:
    token = _anon_token(ip, user_agent)
    row = db.scalar(select(AnonymousId).where(AnonymousId.token == token))
    if row is None:
        row = AnonymousId(token=token)
        db.add(row)
        db.flush()
    else:
        row.last_seen = datetime.now(timezone.utc)
        row.session_count += 1
    return row


@router.post("/events", response_model=UsageEventOut, status_code=status.HTTP_201_CREATED)
def track_event(
    payload: UsageEventIn,
    request: Request,
    db: Session = Depends(get_db),
    authorization: str | None = Header(default=None),
    x_forwarded_for: str | None = Header(default=None),
    user_agent: str = Header(default=""),
) -> UsageEvent:
    """Record a usage event from a registered or anonymous visitor.

    This endpoint is intentionally unauthenticated — anonymous visitors must
    be able to record events. A registered visitor is identified by their
    bearer token when one is supplied.
    """
    try:
        event_type = UsageEventType(payload.event_type)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unknown event_type '{payload.event_type}'",
        ) from None

    user_id: int | None = None
    if authorization and authorization.lower().startswith("bearer "):
        # Best-effort: an invalid token still records an anonymous event.
        from app.security import decode_token
        from fastapi import HTTPException as _HE

        try:
            claims = decode_token(authorization.split(" ", 1)[1], expected_type="access")
            user_id = int(claims.get("sub", "0")) or None
        except (_HE, TypeError, ValueError):
            user_id = None

    ip = _client_ip(request, x_forwarded_for)
    anon = _resolve_anonymous_id(db, ip, user_agent)

    event = UsageEvent(
        occurred_at=datetime.now(timezone.utc),
        event_type=event_type,
        content_id=payload.content_id,
        asset_id=payload.asset_id,
        user_id=user_id,
        anonymous_id=anon.id if anon else None,
        session_id=payload.session_id,
        watch_seconds=payload.watch_seconds,
        completion_ratio=payload.completion_ratio,
        referrer=(payload.referrer or "")[:400] or None,
        device=(payload.device or "")[:80] or None,
        country=(payload.country or "")[:8] or None,
        extra=payload.extra,
    )
    db.add(event)
    db.commit()
    db.refresh(event)
    return event


@router.get("/events", response_model=Page[UsageEventOut])
def list_events(
    db: Session = Depends(get_db),
    user: User = Depends(require_role(*ANALYTICS_ROLES)),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    content_id: int | None = None,
    user_id: int | None = None,
    event_type: str | None = None,
    since_hours: int = Query(default=24, ge=1, le=24 * 90),
) -> Page[UsageEventOut]:
    """List raw usage events (admin/analytics use).

    AUTHENTICATED. The raw event stream carries `user_id`, so it exposes
    who watched what. It was previously readable by anyone, which leaked
    the registered-viewer identity graph to unauthenticated callers.
    """
    stmt = select(UsageEvent)
    if content_id is not None:
        stmt = stmt.where(UsageEvent.content_id == content_id)
    if user_id is not None:
        stmt = stmt.where(UsageEvent.user_id == user_id)
    if event_type:
        stmt = stmt.where(UsageEvent.event_type == UsageEventType(event_type))
    cutoff = datetime.now(timezone.utc) - timedelta(hours=since_hours)
    stmt = stmt.where(UsageEvent.occurred_at >= cutoff)

    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(
        stmt.order_by(UsageEvent.occurred_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return Page[UsageEventOut](
        items=[UsageEventOut.model_validate(r) for r in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/summary")
def usage_summary(
    db: Session = Depends(get_db),
    since_hours: int = Query(default=24, ge=1, le=24 * 90),
) -> dict:
    """Aggregate funnel metrics across registered and anonymous visitors.

    This endpoint is aggregate-only: it returns counts and totals, never
    identities. It is deliberately unauthenticated so the Streamlit
    dashboard can read it without holding a credential.

    Returns, for the window:
      * unique_registered / unique_anonymous viewers
      * total watch seconds
      * average completion ratio
      * per-event-type counts
    """
    cutoff = datetime.now(timezone.utc) - timedelta(hours=since_hours)
    base = select(UsageEvent).where(UsageEvent.occurred_at >= cutoff)

    total_events = db.scalar(select(func.count()).select_from(base.subquery())) or 0
    watch_seconds = (
        db.scalar(
            select(func.coalesce(func.sum(UsageEvent.watch_seconds), 0)).where(
                UsageEvent.occurred_at >= cutoff
            )
        )
        or 0
    )
    avg_completion = (
        db.scalar(
            select(func.coalesce(func.avg(UsageEvent.completion_ratio), 0.0)).where(
                UsageEvent.occurred_at >= cutoff
            )
        )
        or 0.0
    )
    unique_registered = (
        db.scalar(
            select(func.count(func.distinct(UsageEvent.user_id))).where(
                UsageEvent.occurred_at >= cutoff,
                UsageEvent.user_id.is_not(None),
            )
        )
        or 0
    )
    unique_anonymous = (
        db.scalar(
            select(func.count(func.distinct(UsageEvent.anonymous_id))).where(
                UsageEvent.occurred_at >= cutoff,
                UsageEvent.user_id.is_(None),
                UsageEvent.anonymous_id.is_not(None),
            )
        )
        or 0
    )

    by_type_rows = db.execute(
        select(UsageEvent.event_type, func.count())
        .where(UsageEvent.occurred_at >= cutoff)
        .group_by(UsageEvent.event_type)
    ).all()

    # The grouped column comes back as the Python enum, not its value.
    by_type: dict[str, int] = {}
    for event_type, count in by_type_rows:
        key = getattr(event_type, "value", event_type)
        by_type[str(key)] = int(count)

    top_content_rows = db.execute(
        select(
            Content.id,
            Content.title,
            func.count(UsageEvent.id),
            func.coalesce(func.sum(UsageEvent.watch_seconds), 0),
        )
        .join(UsageEvent, UsageEvent.content_id == Content.id)
        .where(UsageEvent.occurred_at >= cutoff)
        .group_by(Content.id, Content.title)
        .order_by(func.count(UsageEvent.id).desc())
        .limit(10)
    ).all()

    return {
        "window_hours": since_hours,
        "since": cutoff.isoformat(),
        "total_events": total_events,
        "watch_seconds": int(watch_seconds),
        "avg_completion_ratio": round(float(avg_completion), 4),
        "unique_registered_viewers": int(unique_registered),
        "unique_anonymous_viewers": int(unique_anonymous),
        "events_by_type": by_type,
        "top_content": [
            {
                "content_id": cid,
                "title": title,
                "events": int(events),
                "watch_seconds": int(secs),
            }
            for cid, title, events, secs in top_content_rows
        ],
    }
