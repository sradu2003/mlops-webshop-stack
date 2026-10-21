"""Unit tests for request/response schema validation.

These do not need a database.
"""

from __future__ import annotations

import os

import pytest
from pydantic import ValidationError

os.environ.setdefault("JWT_SECRET", "test-secret-for-unit-tests-0123456789")

from app.schemas import (  # noqa: E402
    ContentIn,
    ContentUpdate,
    UsageEventIn,
    UserRegisterIn,
)


class TestUserRegister:
    def test_valid(self):
        u = UserRegisterIn(email="a@example.com", password="Passw0rd!", display_name="Ada")
        assert u.display_name == "Ada"

    def test_weak_password_rejected(self):
        with pytest.raises(ValidationError):
            UserRegisterIn(email="a@example.com", password="password", display_name="Ada")

    def test_short_display_name_rejected(self):
        with pytest.raises(ValidationError):
            UserRegisterIn(email="a@example.com", password="Passw0rd!", display_name="A")

    def test_invalid_email_rejected(self):
        with pytest.raises(ValidationError):
            UserRegisterIn(email="not-an-email", password="Passw0rd!", display_name="Ada")

    def test_display_name_stripped(self):
        u = UserRegisterIn(email="a@example.com", password="Passw0rd!", display_name="  Ada  ")
        assert u.display_name == "Ada"


class TestContentIn:
    def test_valid(self):
        c = ContentIn(title="Intro to MLOps", slug="intro-to-mlops", price_cents=1999)
        assert c.currency == "EUR"
        assert c.price_cents == 1999

    def test_negative_price_rejected(self):
        """A negative price would let an author credit a buyer."""
        with pytest.raises(ValidationError):
            ContentIn(title="Bad", slug="bad-slug", price_cents=-1)

    def test_absurd_price_rejected(self):
        with pytest.raises(ValidationError):
            ContentIn(title="Bad", slug="bad-slug", price_cents=10**12)

    def test_invalid_slug_rejected(self):
        with pytest.raises(ValidationError):
            ContentIn(title="Bad", slug="Not A Slug!")

    def test_uppercase_slug_rejected(self):
        with pytest.raises(ValidationError):
            ContentIn(title="Bad", slug="NotLowercase")

    def test_short_title_rejected(self):
        with pytest.raises(ValidationError):
            ContentIn(title="ab", slug="some-slug")

    def test_currency_uppercased(self):
        c = ContentIn(title="Title Here", slug="some-slug", currency="usd")
        assert c.currency == "USD"


class TestContentUpdate:
    def test_all_optional(self):
        u = ContentUpdate()
        assert u.title is None
        assert u.price_cents is None

    def test_negative_price_rejected(self):
        with pytest.raises(ValidationError):
            ContentUpdate(price_cents=-1)

    def test_model_dump_exclude_unset(self):
        u = ContentUpdate(title="New Title")
        assert u.model_dump(exclude_unset=True) == {"title": "New Title"}


class TestUsageEventIn:
    def test_ratio_bounded(self):
        with pytest.raises(ValidationError):
            UsageEventIn(event_type="play", completion_ratio=1.5)
        with pytest.raises(ValidationError):
            UsageEventIn(event_type="play", completion_ratio=-0.1)

    def test_ratio_in_range(self):
        e = UsageEventIn(event_type="play", completion_ratio=0.75)
        assert e.completion_ratio == 0.75

    def test_negative_watch_seconds_rejected(self):
        with pytest.raises(ValidationError):
            UsageEventIn(event_type="play", watch_seconds=-1)
