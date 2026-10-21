"""API integration tests.

These exercise the real HTTP surface against a real PostgreSQL database.
They cover the behaviour the unit tests cannot: authentication,
authorisation, validation, co-authoring permissions, telemetry and the
asset lifecycle.

REQUIREMENTS
------------
A PostgreSQL instance with the pgvector extension available:

    docker run -d --name test-pg -p 5432:5432 \
      -e POSTGRES_USER=shop -e POSTGRES_PASSWORD=shop -e POSTGRES_DB=shopdb \
      pgvector/pgvector:pg16

Environment:

    export DATABASE_URL="postgresql+psycopg://shop:shop@127.0.0.1:5432/shopdb"
    export JWT_SECRET=$(openssl rand -hex 32)
    export ANON_SALT=$(openssl rand -hex 32)
    pytest -q tests/integration/ -m integration

These tests are marked `integration` and are EXCLUDED from the default
unit-test run (see `pytest.ini`), so `pytest -q tests/` never needs a
database.
"""

from __future__ import annotations

import os

import pytest

# The settings are validated at import time, so the secrets must exist
# before app.config is imported.
os.environ.setdefault("JWT_SECRET", "integration-test-secret-0123456789abcdef")
os.environ.setdefault("ANON_SALT", "integration-test-anon-salt-0123456789abcdef")
os.environ.setdefault("ENVIRONMENT", "development")

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app.db import Base, SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import User  # noqa: E402

pytestmark = pytest.mark.integration


@pytest.fixture()
def client():
    """A TestClient over a schema that is empty for this test only.

    Function scope matters: several assertions depend on there being no
    pre-existing rows (for example, that the FIRST registered account is
    promoted to `owner`).
    """
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    with TestClient(app) as c:
        yield c
    Base.metadata.drop_all(bind=engine)


def register(client, email: str, password: str = "Testpass123"):
    """Register a user and return (response_body, access_token)."""
    resp = client.post(
        "/api/auth/register",
        json={"email": email, "password": password, "display_name": email.split("@")[0]},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    return body, body["access_token"]


def set_role(email: str, role: str) -> None:
    """Change a user's role directly in the database.

    The platform has no role-assignment endpoint (it is an admin task
    done at the database level), so the tests do the same thing.
    """
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == email))
        assert user is not None, f"no such user: {email}"
        user.role = role
        db.commit()


def promote(email: str, role: str) -> None:
    """Grant a role (kept for readability at the call sites)."""
    set_role(email, role)


def register_plain_user(client, email: str, password: str = "Testpass123"):
    """Register a user whose role is exactly `user`.

    The FIRST account on an empty database is auto-promoted to `owner`
    (that is how the platform is bootstrapped). A test that needs an
    ordinary account must therefore either not be the first, or must
    have its role set explicitly — otherwise it silently exercises the
    owner path and asserts the wrong thing.
    """
    _, token = register(client, email, password)
    set_role(email, "user")
    return token


def auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------


class TestAuth:
    def test_register_returns_token_pair(self, client):
        body, _ = register(client, "auth1@example.com")
        assert body["token_type"] == "bearer"
        assert body["expires_in"] > 0
        assert body["access_token"] and body["refresh_token"]

    def test_first_user_is_promoted_to_owner(self, client):
        """The bootstrap account must be able to administer the platform."""
        body, token = register(client, "owner@example.com")
        me = client.get("/api/auth/me", headers=auth(token))
        assert me.status_code == 200
        assert me.json()["role"] == "owner", me.json()

    def test_second_user_is_not_promoted(self, client):
        register(client, "first@example.com")
        _, token = register(client, "second@example.com")
        me = client.get("/api/auth/me", headers=auth(token))
        assert me.json()["role"] == "user"

    def test_duplicate_email_rejected(self, client):
        register(client, "dupe@example.com")
        resp = client.post(
            "/api/auth/register",
            json={
                "email": "dupe@example.com",
                "password": "Testpass123",
                "display_name": "dupe",
            },
        )
        assert resp.status_code == 409

    def test_weak_password_rejected(self, client):
        resp = client.post(
            "/api/auth/register",
            json={"email": "weak@example.com", "password": "short", "display_name": "w"},
        )
        assert resp.status_code == 422

    def test_password_without_digit_rejected(self, client):
        resp = client.post(
            "/api/auth/register",
            json={
                "email": "nodigit@example.com",
                "password": "onlyletters",
                "display_name": "n",
            },
        )
        assert resp.status_code == 422

    def test_login_succeeds(self, client):
        register(client, "login@example.com")
        resp = client.post(
            "/api/auth/login",
            json={"email": "login@example.com", "password": "Testpass123"},
        )
        assert resp.status_code == 200
        assert resp.json()["access_token"]

    def test_login_wrong_password_rejected(self, client):
        register(client, "wrongpw@example.com")
        resp = client.post(
            "/api/auth/login",
            json={"email": "wrongpw@example.com", "password": "Wrongpass123"},
        )
        assert resp.status_code == 401

    def test_login_unknown_user_same_status_as_wrong_password(self, client):
        """No user enumeration: both cases must be 401 with the same body."""
        register(client, "enum1@example.com")
        unknown = client.post(
            "/api/auth/login",
            json={"email": "nobody@example.com", "password": "Testpass123"},
        )
        wrong = client.post(
            "/api/auth/login",
            json={"email": "enum1@example.com", "password": "Wrongpass123"},
        )
        assert unknown.status_code == wrong.status_code == 401
        assert unknown.json()["detail"] == wrong.json()["detail"]

    def test_me_requires_token(self, client):
        assert client.get("/api/auth/me").status_code == 401

    def test_me_rejects_garbage_token(self, client):
        assert client.get("/api/auth/me", headers=auth("not.a.jwt")).status_code == 401

    def test_access_token_not_accepted_as_refresh(self, client):
        _, token = register(client, "typemix@example.com")
        resp = client.post("/api/auth/refresh", params={"token": token})
        assert resp.status_code == 401

    def test_refresh_issues_new_tokens(self, client):
        body, _ = register(client, "refresh@example.com")
        resp = client.post("/api/auth/refresh", params={"token": body["refresh_token"]})
        assert resp.status_code == 200
        assert resp.json()["access_token"]

    def test_password_is_not_stored_in_plaintext(self, client):
        register(client, "hash@example.com", "Secretpass123")
        with SessionLocal() as db:
            user = db.scalar(select(User).where(User.email == "hash@example.com"))
            assert user is not None
            assert "Secretpass123" not in user.password_hash
            assert user.password_hash.startswith("pbkdf2_sha256$")


# ---------------------------------------------------------------------------
# Content & co-authoring
# ---------------------------------------------------------------------------


class TestContent:
    def test_create_requires_writer_role(self, client):
        """A plain 'user' may not publish content."""
        token = register_plain_user(client, "plainuser@example.com")
        resp = client.post(
            "/api/content",
            headers=auth(token),
            json={"title": "Nope", "slug": "nope-not-allowed"},
        )
        assert resp.status_code == 403, resp.text

    def test_author_can_create(self, client):
        _, token = register(client, "author1@example.com")
        promote("author1@example.com", "author")
        resp = client.post(
            "/api/content",
            headers=auth(token),
            json={
                "title": "Intro to MLOps",
                "slug": "intro-to-mlops",
                "price_cents": 4900,
            },
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["price_cents"] == 4900
        assert body["status"] == "draft"

    def test_negative_price_rejected(self, client):
        _, token = register(client, "negprice@example.com")
        promote("negprice@example.com", "author")
        resp = client.post(
            "/api/content",
            headers=auth(token),
            json={"title": "Negative", "slug": "negative-price", "price_cents": -100},
        )
        assert resp.status_code == 422, resp.text

    def test_invalid_slug_rejected(self, client):
        _, token = register(client, "badslug@example.com")
        promote("badslug@example.com", "author")
        resp = client.post(
            "/api/content",
            headers=auth(token),
            json={"title": "Bad slug", "slug": "Not A Valid Slug!"},
        )
        assert resp.status_code == 422, resp.text

    def test_duplicate_slug_rejected(self, client):
        _, token = register(client, "dupslug@example.com")
        promote("dupslug@example.com", "author")
        payload = {"title": "First", "slug": "same-slug-here"}
        assert client.post("/api/content", headers=auth(token), json=payload).status_code == 201
        resp = client.post(
            "/api/content",
            headers=auth(token),
            json={"title": "Second", "slug": "same-slug-here"},
        )
        assert resp.status_code == 409, resp.text

    def test_public_listing_shows_only_published(self, client):
        resp = client.get("/api/content")
        assert resp.status_code == 200
        for item in resp.json()["items"]:
            assert item["status"] == "published"

    def test_draft_is_not_publicly_readable(self, client):
        """A draft must not leak to anonymous callers."""
        _, token = register(client, "drafter@example.com")
        promote("drafter@example.com", "author")
        created = client.post(
            "/api/content",
            headers=auth(token),
            json={"title": "Secret plans", "slug": "secret-plans-doc"},
        )
        content_id = created.json()["id"]
        assert created.json()["status"] == "draft"

        # By id — anonymous
        assert client.get(f"/api/content/{content_id}").status_code == 404
        # By slug — anonymous
        assert client.get("/api/content/slug/secret-plans-doc").status_code == 404
        # By id — a different signed-in user
        other = register_plain_user(client, "other-drafter@example.com")
        assert client.get(f"/api/content/{content_id}", headers=auth(other)).status_code == 404
        # By id — the author
        assert client.get(f"/api/content/{content_id}", headers=auth(token)).status_code == 200

    def test_draft_listing_is_rejected(self, client):
        """`?status=draft` must not list other people's unfinished work."""
        resp = client.get("/api/content?status=draft")
        assert resp.status_code == 403, resp.text

    def test_unknown_status_is_422_not_500(self, client):
        resp = client.get("/api/content?status=bogus-status")
        assert resp.status_code == 422, resp.text

    def test_update_requires_ownership(self, client):
        _, author_token = register(client, "owner2@example.com")
        _, other_token = register(client, "intruder@example.com")
        promote("owner2@example.com", "author")

        created = client.post(
            "/api/content",
            headers=auth(author_token),
            json={"title": "Mine", "slug": "mine-not-yours"},
        )
        content_id = created.json()["id"]

        resp = client.patch(
            f"/api/content/{content_id}",
            headers=auth(other_token),
            json={"title": "Hijacked"},
        )
        assert resp.status_code == 403, resp.text

    def test_coauthor_can_edit(self, client):
        _, author_token = register(client, "coauthor-owner@example.com")
        _, co_token = register(client, "coauthor@example.com")
        promote("coauthor-owner@example.com", "author")

        created = client.post(
            "/api/content",
            headers=auth(author_token),
            json={"title": "Shared", "slug": "shared-content-item"},
        )
        content_id = created.json()["id"]
        me = client.get("/api/auth/me", headers=auth(co_token)).json()

        add = client.post(
            f"/api/content/{content_id}/contributions",
            headers=auth(author_token),
            json={"contributor_id": me["id"], "role": "co_author"},
        )
        assert add.status_code == 201, add.text

        edit = client.patch(
            f"/api/content/{content_id}",
            headers=auth(co_token),
            json={"summary": "Edited by a co-author"},
        )
        assert edit.status_code == 200, edit.text
        assert edit.json()["summary"] == "Edited by a co-author"

    def test_only_owner_can_add_contributors(self, client):
        _, author_token = register(client, "solo-owner@example.com")
        _, other_token = register(client, "not-owner@example.com")
        promote("solo-owner@example.com", "author")

        created = client.post(
            "/api/content",
            headers=auth(author_token),
            json={"title": "Solo", "slug": "solo-content-item"},
        )
        content_id = created.json()["id"]
        me = client.get("/api/auth/me", headers=auth(other_token)).json()

        resp = client.post(
            f"/api/content/{content_id}/contributions",
            headers=auth(other_token),
            json={"contributor_id": me["id"]},
        )
        assert resp.status_code == 403, resp.text

    def test_revision_created(self, client):
        _, token = register(client, "revuser@example.com")
        promote("revuser@example.com", "author")
        created = client.post(
            "/api/content",
            headers=auth(token),
            json={"title": "Versioned", "slug": "versioned-content-item"},
        )
        content_id = created.json()["id"]
        resp = client.post(f"/api/content/{content_id}/revisions", headers=auth(token))
        assert resp.status_code == 201, resp.text

    def test_delete_requires_ownership(self, client):
        _, token = register(client, "deleter@example.com")
        _, other = register(client, "keepalive@example.com")
        promote("deleter@example.com", "author")

        created = client.post(
            "/api/content",
            headers=auth(token),
            json={"title": "Deletable", "slug": "deletable-content-item"},
        )
        content_id = created.json()["id"]

        assert client.delete(f"/api/content/{content_id}", headers=auth(other)).status_code == 403
        assert client.delete(f"/api/content/{content_id}", headers=auth(token)).status_code == 204


# ---------------------------------------------------------------------------
# Assets
# ---------------------------------------------------------------------------


class TestAssets:
    def test_register_asset_requires_auth(self, client):
        resp = client.post(
            "/api/assets",
            json={"content_id": 1, "filename": "video.mp4", "kind": "video"},
        )
        assert resp.status_code == 401

    def test_asset_lifecycle(self, client):
        _, token = register(client, "assetuser@example.com")
        promote("assetuser@example.com", "author")

        created = client.post(
            "/api/content",
            headers=auth(token),
            json={"title": "With assets", "slug": "content-with-assets"},
        )
        content_id = created.json()["id"]

        reg = client.post(
            "/api/assets",
            headers=auth(token),
            json={
                "content_id": content_id,
                "filename": "lesson-01.mp4",
                "kind": "video",
                "mime_type": "video/mp4",
                "size_bytes": 1024,
            },
        )
        assert reg.status_code == 201, reg.text
        asset_id = reg.json()["id"]
        assert reg.json()["status"] == "uploading"

        ready = client.post(f"/api/assets/{asset_id}/complete", headers=auth(token))
        assert ready.status_code == 200, ready.text
        assert ready.json()["status"] == "ready"

    def test_negative_size_rejected(self, client):
        _, token = register(client, "negsize@example.com")
        resp = client.post(
            "/api/assets",
            headers=auth(token),
            json={"content_id": 1, "filename": "x.mp4", "size_bytes": -1},
        )
        assert resp.status_code == 422

    def test_cannot_attach_asset_to_someone_elses_content(self, client):
        """A stranger must not inject media into another author's work."""
        _, author_token = register(client, "media-owner@example.com")
        promote("media-owner@example.com", "author")
        created = client.post(
            "/api/content",
            headers=auth(author_token),
            json={"title": "My course", "slug": "my-course-media"},
        )
        content_id = created.json()["id"]

        intruder = register_plain_user(client, "media-intruder@example.com")
        resp = client.post(
            "/api/assets",
            headers=auth(intruder),
            json={
                "content_id": content_id,
                "filename": "malicious.mp4",
                "kind": "video",
            },
        )
        assert resp.status_code == 403, resp.text

    def test_coauthor_can_attach_asset(self, client):
        """An accepted co-author may contribute media."""
        _, author_token = register(client, "co-media-owner@example.com")
        _, co_token = register(client, "co-media-user@example.com")
        promote("co-media-owner@example.com", "author")

        created = client.post(
            "/api/content",
            headers=auth(author_token),
            json={"title": "Shared course", "slug": "shared-course-media"},
        )
        content_id = created.json()["id"]
        me = client.get("/api/auth/me", headers=auth(co_token)).json()
        add = client.post(
            f"/api/content/{content_id}/contributions",
            headers=auth(author_token),
            json={"contributor_id": me["id"], "role": "co_author"},
        )
        assert add.status_code == 201, add.text

        resp = client.post(
            "/api/assets",
            headers=auth(co_token),
            json={
                "content_id": content_id,
                "filename": "lesson.mp4",
                "kind": "video",
            },
        )
        assert resp.status_code == 201, resp.text


# ---------------------------------------------------------------------------
# Usage telemetry & monetisation
# ---------------------------------------------------------------------------


class TestUsage:
    def test_anonymous_event_accepted_without_auth(self, client):
        resp = client.post("/api/usage/events", json={"event_type": "page_view"})
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["event_type"] == "page_view"
        assert body["user_id"] is None

    def test_unknown_event_type_rejected(self, client):
        resp = client.post("/api/usage/events", json={"event_type": "not_a_real_event"})
        assert resp.status_code == 422

    def test_completion_ratio_bounded(self, client):
        resp = client.post(
            "/api/usage/events",
            json={"event_type": "play", "completion_ratio": 5.0},
        )
        assert resp.status_code == 422

    def test_registered_event_attributed_to_user(self, client):
        _, token = register(client, "watcher@example.com")
        resp = client.post(
            "/api/usage/events",
            headers=auth(token),
            json={"event_type": "play", "watch_seconds": 120},
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["user_id"] is not None

    def test_summary_is_public_and_aggregate(self, client):
        client.post("/api/usage/events", json={"event_type": "page_view"})
        resp = client.get("/api/usage/summary?since_hours=24")
        assert resp.status_code == 200
        body = resp.json()
        assert "total_events" in body
        assert "events_by_type" in body
        # An aggregate endpoint must not leak identities.
        assert "user_id" not in body

    def test_raw_events_require_auth(self, client):
        """The raw event stream carries user_id and must be protected."""
        resp = client.get("/api/usage/events")
        assert resp.status_code == 401, resp.text

    def test_raw_events_require_analytics_role(self, client):
        """A plain registered user is still not allowed."""
        token = register_plain_user(client, "nosy@example.com")
        resp = client.get("/api/usage/events", headers=auth(token))
        assert resp.status_code == 403, resp.text

    def test_curator_can_read_raw_events(self, client):
        _, token = register(client, "curator@example.com")
        promote("curator@example.com", "curator")
        resp = client.get("/api/usage/events", headers=auth(token))
        assert resp.status_code == 200, resp.text
        assert "items" in resp.json()


# ---------------------------------------------------------------------------
# Operational endpoints
# ---------------------------------------------------------------------------


class TestOps:
    def test_health_is_public(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    def test_ready_checks_the_database(self, client):
        resp = client.get("/ready")
        assert resp.status_code == 200
        assert resp.json()["database"] == "reachable"

    def test_metrics_endpoint_exposes_prometheus_text(self, client):
        resp = client.get("/metrics")
        assert resp.status_code == 200
        assert "http_requests_total" in resp.text

    def test_inflight_gauge_is_registered(self, client):
        """The in-flight gauge is OFF by default in the instrumentator.

        Without `should_instrument_requests_inprogress=True` the series is
        never registered, and the `service:http_requests_in_flight`
        recording rule silently returns an empty vector.
        """
        client.get("/health")
        resp = client.get("/metrics")
        assert "http_requests_inprogress" in resp.text
        assert "# TYPE http_requests_inprogress gauge" in resp.text
