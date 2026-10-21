"""Unit tests for password hashing and JWT handling.

These do not need a database.
"""

from __future__ import annotations

import os

import pytest
from fastapi import HTTPException

os.environ.setdefault("JWT_SECRET", "test-secret-for-unit-tests-0123456789")
# The pseudonymisation salt is validated exactly like JWT_SECRET, so a
# placeholder must be replaced before app.config is imported.
os.environ.setdefault("ANON_SALT", "test-anon-salt-for-unit-tests-0123456789")

from app.security import (  # noqa: E402
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)


class TestPasswordHashing:
    def test_roundtrip(self):
        encoded = hash_password("correct horse battery staple")
        assert verify_password("correct horse battery staple", encoded)

    def test_wrong_password_rejected(self):
        encoded = hash_password("correct horse battery staple")
        assert not verify_password("wrong password", encoded)

    def test_hash_is_salted(self):
        """Two hashes of the same password must differ."""
        a = hash_password("samepassword1")
        b = hash_password("samepassword1")
        assert a != b
        assert verify_password("samepassword1", a)
        assert verify_password("samepassword1", b)

    def test_short_password_rejected(self):
        with pytest.raises(ValueError):
            hash_password("short")

    @pytest.mark.parametrize(
        "malformed",
        ["", "notahash", "pbkdf2_sha256$abc$def$ghi", "a$b$c$d$e"],
    )
    def test_malformed_hash_returns_false_not_raises(self, malformed):
        assert verify_password("whatever", malformed) is False


class TestTokens:
    def test_access_token_roundtrip(self):
        token = create_access_token("42", role="admin")
        payload = decode_token(token, expected_type="access")
        assert payload["sub"] == "42"
        assert payload["role"] == "admin"
        assert payload["typ"] == "access"

    def test_refresh_token_roundtrip(self):
        token = create_refresh_token("42")
        payload = decode_token(token, expected_type="refresh")
        assert payload["typ"] == "refresh"

    def test_access_token_rejected_as_refresh(self):
        token = create_access_token("42")
        with pytest.raises(HTTPException) as exc:
            decode_token(token, expected_type="refresh")
        assert exc.value.status_code == 401

    def test_refresh_token_rejected_as_access(self):
        token = create_refresh_token("42")
        with pytest.raises(HTTPException) as exc:
            decode_token(token, expected_type="access")
        assert exc.value.status_code == 401

    def test_garbage_token_rejected(self):
        with pytest.raises(HTTPException) as exc:
            decode_token("not.a.jwt", expected_type="access")
        assert exc.value.status_code == 401

    def test_tampered_token_rejected(self):
        token = create_access_token("42")
        # Flip a character in the signature.
        tampered = token[:-2] + ("AA" if not token.endswith("AA") else "BB")
        with pytest.raises(HTTPException):
            decode_token(tampered, expected_type="access")

    def test_tokens_are_unique(self):
        """Each token must have a unique jti so refresh tokens are distinct."""
        a = create_refresh_token("42")
        b = create_refresh_token("42")
        assert a != b
