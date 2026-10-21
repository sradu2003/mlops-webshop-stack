"""Configuration validation tests."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.config import Settings

# A valid ANON_SALT is required for every Settings() construction because
# the placeholder is rejected at validation time (same rule as JWT_SECRET).
VALID_ANON = "a-real-anon-salt-value"


class TestSettings:
    def test_rejects_placeholder_jwt_secret(self):
        """A default 'dev' secret must never be accepted."""
        with pytest.raises(ValidationError):
            Settings(jwt_secret="dev", anon_salt=VALID_ANON)
        with pytest.raises(ValidationError):
            Settings(jwt_secret="change_me_jwt_secret", anon_salt=VALID_ANON)
        with pytest.raises(ValidationError):
            Settings(jwt_secret="", anon_salt=VALID_ANON)

    def test_rejects_placeholder_anon_salt(self):
        """The pseudonymisation salt is a secret; placeholders must fail.

        With a known salt, the anonymous visitor tokens can be recomputed
        offline and the traffic attributed to individuals.
        """
        for bad in ("", "dev", "change_me_anon_salt", "change-me-anon-salt"):
            with pytest.raises(ValidationError):
                Settings(jwt_secret="a-real-secret-value", anon_salt=bad)

    def test_rejects_unknown_environment(self):
        with pytest.raises(ValidationError):
            Settings(
                jwt_secret="a-real-secret-value",
                anon_salt=VALID_ANON,
                environment="stagingg",
            )

    def test_accepts_valid_settings(self):
        s = Settings(
            jwt_secret="a-real-secret-value",
            anon_salt=VALID_ANON,
            environment="production",
        )
        assert s.is_production is True
        assert s.environment == "production"

    def test_cors_origin_parsing(self):
        s = Settings(
            jwt_secret="a-real-secret-value",
            anon_salt=VALID_ANON,
            cors_origins="https://a.com, https://b.com ,",
        )
        assert s.cors_origin_list == ["https://a.com", "https://b.com"]

    def test_empty_cors_yields_empty_list(self):
        """Empty CORS must mean 'no browser origins', not 'any origin'."""
        s = Settings(jwt_secret="a-real-secret-value", anon_salt=VALID_ANON)
        assert s.cors_origin_list == []

    def test_allowed_hosts_parsing(self):
        s = Settings(
            jwt_secret="a-real-secret-value",
            anon_salt=VALID_ANON,
            allowed_hosts="api.example.com, example.com",
        )
        assert s.allowed_host_list == ["api.example.com", "example.com"]
