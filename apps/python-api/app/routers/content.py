"""Content, co-author contribution and revision routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import (
    Contribution,
    Content,
    ContentStatus,
    ContentType,
    Revision,
    User,
)
from app.permissions import assert_can_edit, can_view_unpublished
from app.schemas import (
    ContentIn,
    ContentOut,
    ContentUpdate,
    ContributionIn,
    ContributionOut,
    Page,
)
from app.security import get_current_user, get_current_user_optional, require_role

router = APIRouter(prefix="/api/content", tags=["content"])

WRITER_ROLES = {"author", "curator", "admin", "owner"}


def _get_content_or_404(db: Session, content_id: int) -> Content:
    content = db.get(Content, content_id)
    if content is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    return content


def _can_view_unpublished(content: Content, user: User | None) -> bool:
    """Whether `user` may see a non-published content item.

    See `app.permissions.can_view_unpublished` for the rule.
    """
    return can_view_unpublished(content, user)


def _get_visible_content(
    db: Session,
    content_id: int,
    user: User | None,
) -> Content:
    """Fetch a content item, hiding unpublished ones from the public.

    `GET /api/content/{id}` and `GET /api/content/slug/{slug}` used to
    return ANY row, including drafts and items still in review. That
    leaked unreleased work to anyone who guessed an id or slug.
    """
    content = _get_content_or_404(db, content_id)
    if content.status != ContentStatus.published and not _can_view_unpublished(
        content, user
    ):
        # 404, not 403: a public caller should not learn that an
        # unpublished item exists at all.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    return content


def _assert_can_edit(content: Content, user: User) -> None:
    """Owner, admin, curator, or an accepted contributor may edit."""
    assert_can_edit(content, user)


@router.get("", response_model=Page[ContentOut])
def list_content(
    db: Session = Depends(get_db),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    content_status: str | None = Query(default=None, alias="status"),
    author_id: int | None = None,
    q: str | None = Query(default=None, max_length=200),
) -> Page[ContentOut]:
    """Paginated listing. Public-facing results are limited to published items.

    The `status` filter is only honoured for published items: asking for
    `?status=draft` must not expose other people's unfinished work.
    Previously this endpoint applied the filter unconditionally, so
    `?status=draft` listed every draft in the platform.
    """
    stmt = select(Content)
    if content_status:
        try:
            wanted = ContentStatus(content_status)
        except ValueError:
            # An unknown status must be a 422, not an unhandled ValueError
            # that surfaces as a 500.
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    "Unknown status. Valid values: "
                    + ", ".join(s.value for s in ContentStatus)
                ),
            ) from None
        if wanted != ContentStatus.published:
            # Non-published listings are an admin capability; the public
            # listing is always published-only.
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only published content can be listed publicly",
            )
        stmt = stmt.where(Content.status == wanted)
    else:
        stmt = stmt.where(Content.status == ContentStatus.published)
    if author_id is not None:
        stmt = stmt.where(Content.author_id == author_id)
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(Content.title.ilike(like) | Content.summary.ilike(like))

    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(
        stmt.order_by(Content.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return Page[ContentOut](
        items=[ContentOut.model_validate(r) for r in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/{content_id}", response_model=ContentOut)
def get_content(
    content_id: int,
    db: Session = Depends(get_db),
    user: User | None = Depends(get_current_user_optional),
) -> Content:
    """Fetch one item. Unpublished items are visible only to their authors."""
    return _get_visible_content(db, content_id, user)


@router.get("/slug/{slug}", response_model=ContentOut)
def get_content_by_slug(
    slug: str,
    db: Session = Depends(get_db),
    user: User | None = Depends(get_current_user_optional),
) -> Content:
    row = db.scalar(select(Content).where(Content.slug == slug))
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    if row.status != ContentStatus.published and not _can_view_unpublished(row, user):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    return row


@router.post("", response_model=ContentOut, status_code=status.HTTP_201_CREATED)
def create_content(
    payload: ContentIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(*WRITER_ROLES)),
) -> Content:
    """Create a draft content item owned by the caller."""
    if db.scalar(select(Content.id).where(Content.slug == payload.slug)) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Slug already in use"
        )
    content = Content(
        title=payload.title,
        slug=payload.slug,
        summary=payload.summary,
        body=payload.body,
        content_type=ContentType(payload.content_type),
        price_cents=payload.price_cents,
        currency=payload.currency,
        tags=payload.tags,
        author_id=user.id,
        status=ContentStatus.draft,
    )
    db.add(content)
    db.commit()
    db.refresh(content)
    return content


@router.patch("/{content_id}", response_model=ContentOut)
def update_content(
    content_id: int,
    payload: ContentUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Content:
    """Partial update. Publishing requires a price of 0 or a listed product."""
    content = _get_content_or_404(db, content_id)
    _assert_can_edit(content, user)

    data = payload.model_dump(exclude_unset=True)
    if "status" in data and data["status"] is not None:
        new_status = ContentStatus(data["status"])
        if new_status == ContentStatus.published:
            if not content.author_id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST, detail="Content has no author"
                )
        content.status = new_status
        if new_status == ContentStatus.published and content.published_at is None:
            from app.models import _utcnow

            content.published_at = _utcnow()
        data.pop("status")

    for field, value in data.items():
        if field == "content_type" and value is not None:
            value = ContentType(value)
        setattr(content, field, value)

    db.commit()
    db.refresh(content)
    return content


@router.delete(
    "/{content_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
    response_class=Response,
)
def delete_content(
    content_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> None:
    content = _get_content_or_404(db, content_id)
    if user.role.value not in {"admin", "owner"} and content.author_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Not permitted"
        )
    db.delete(content)
    db.commit()


# ---------------------------------------------------------------------------
# Co-authorship
# ---------------------------------------------------------------------------


@router.get(
    "/{content_id}/contributions", response_model=list[ContributionOut]
)
def list_contributions(
    content_id: int, db: Session = Depends(get_db)
) -> list[Contribution]:
    _get_content_or_404(db, content_id)
    return list(
        db.scalars(
            select(Contribution).where(Contribution.content_id == content_id)
        ).all()
    )


@router.post(
    "/{content_id}/contributions",
    response_model=ContributionOut,
    status_code=status.HTTP_201_CREATED,
)
def add_contribution(
    content_id: int,
    payload: ContributionIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Contribution:
    """Add a co-author. Only the content owner or an admin may do this."""
    content = _get_content_or_404(db, content_id)
    if user.role.value not in {"admin", "owner"} and content.author_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the owner can manage contributors",
        )
    if db.get(User, payload.contributor_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Contributor not found"
        )
    existing = db.scalar(
        select(Contribution).where(
            Contribution.content_id == content_id,
            Contribution.contributor_id == payload.contributor_id,
        )
    )
    if existing:
        existing.role = payload.role
        existing.revenue_share_pct = payload.revenue_share_pct
        existing.is_accepted = True
        db.commit()
        db.refresh(existing)
        return existing

    contribution = Contribution(
        content_id=content_id,
        contributor_id=payload.contributor_id,
        role=payload.role,
        revenue_share_pct=payload.revenue_share_pct,
    )
    db.add(contribution)
    db.commit()
    db.refresh(contribution)
    return contribution


@router.delete(
    "/{content_id}/contributions/{contribution_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
    response_class=Response,
)
def remove_contribution(
    content_id: int,
    contribution_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> None:
    content = _get_content_or_404(db, content_id)
    if user.role.value not in {"admin", "owner"} and content.author_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Not permitted"
        )
    row = db.get(Contribution, contribution_id)
    if row is None or row.content_id != content_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    db.delete(row)
    db.commit()


# ---------------------------------------------------------------------------
# Revisions
# ---------------------------------------------------------------------------


@router.post(
    "/{content_id}/revisions", response_model=ContentOut, status_code=status.HTTP_201_CREATED
)
def create_revision(
    content_id: int,
    change_note: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Content:
    """Snapshot the current state of a content item as a new revision."""
    content = _get_content_or_404(db, content_id)
    _assert_can_edit(content, user)

    last = db.scalar(
        select(func.max(Revision.revision_number)).where(
            Revision.content_id == content_id
        )
    )
    revision = Revision(
        content_id=content_id,
        author_id=user.id,
        revision_number=(last or 0) + 1,
        title=content.title,
        body=content.body,
        change_note=(change_note or "")[:500] or None,
    )
    db.add(revision)
    db.commit()
    db.refresh(content)
    return content
