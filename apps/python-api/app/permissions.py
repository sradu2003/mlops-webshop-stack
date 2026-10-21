"""Shared content-authorisation rules.

These rules were originally inline in `routers/content.py`. The asset
router needs the same rule, and duplicating it in two places is how
authorisation bugs happen: one copy gets fixed, the other does not.

Keeping the rules in one module means a change to "who may edit a
content item" is a single edit that applies everywhere.
"""

from __future__ import annotations

from fastapi import HTTPException, status

from app.models import Content, User

# Roles that may edit ANY content item, not just their own.
ELEVATED_ROLES = {"admin", "owner"}

# Roles that may review and moderate content they do not own.
CURATOR_ROLES = {"curator"}


def is_elevated(user: User) -> bool:
    """Admins and owners bypass per-content ownership checks."""
    return user.role.value in ELEVATED_ROLES


def is_accepted_contributor(content: Content, user: User) -> bool:
    """Whether the user is an accepted contributor on this content item."""
    return any(
        c.contributor_id == user.id and c.is_accepted for c in content.contributions
    )


def can_edit_content(content: Content, user: User) -> bool:
    """Whether the user may modify the given content item.

    True for the author, accepted co-authors, curators, admins and owners.
    """
    if is_elevated(user):
        return True
    if content.author_id == user.id:
        return True
    if user.role.value in CURATOR_ROLES:
        return True
    return is_accepted_contributor(content, user)


def can_view_unpublished(content: Content, user: User | None) -> bool:
    """Whether the user may see a content item that is not published.

    A draft is the author's work in progress. It must not be readable by
    the public, only by the author, accepted co-authors, curators and
    admins.
    """
    if user is None:
        return False
    if is_elevated(user):
        return True
    if user.role.value in CURATOR_ROLES:
        return True
    if content.author_id == user.id:
        return True
    return is_accepted_contributor(content, user)


def assert_can_edit(content: Content, user: User) -> None:
    """Raise 403 unless the user may modify the content item."""
    if not can_edit_content(content, user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not a contributor to this content",
        )
