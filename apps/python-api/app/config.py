"""Core configuration, loaded once, validated at import time.

Fails fast: if a required secret is missing the process must refuse to start
rather than boot with a default like "dev".
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=os.getenv("ENV_FILE", ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Core ---------------------------------------------------------------
    app_name: str = "Shop API"
    app_version: str = "1.0.0"
    environment: str = Field(default="development")

    # --- Database -----------------------------------------------------------
    database_url: str = Field(
        default="postgresql+psycopg://shop:shop@127.0.0.1:5432/shopdb"
    )
    db_pool_size: int = 5
    db_max_overflow: int = 10
    db_pool_timeout: int = 30
    db_pool_recycle: int = 1800

    # --- Auth ---------------------------------------------------------------
    jwt_secret: str = Field(default="")
    jwt_algorithm: str = "HS256"
    access_token_ttl_minutes: int = 60 * 12
    refresh_token_ttl_days: int = 30

    # --- Anonymous visitor pseudonymisation ---------------------------------
    # Salt for the HMAC that turns (IP + User-Agent) into a pseudonymous
    # visitor token. It is a SECRET: with a known salt the anonymous
    # identifiers can be recomputed and the traffic attributed offline.
    # Placeholders are rejected, exactly like jwt_secret.
    anon_salt: str = Field(default="")

    # --- CORS ---------------------------------------------------------------
    cors_origins: str = ""

    # --- Host header validation --------------------------------------------
    # Comma-separated list of accepted Host header values.
    # EMPTY = disabled (default). Keep it disabled unless you also make sure
    # the Prometheus/blackbox probes reach the app with a matching Host header,
    # otherwise /health and /ready return 400 and the container is restarted.
    allowed_hosts: str = ""

    # --- Observability ------------------------------------------------------
    metrics_enabled: bool = True
    metrics_path: str = "/metrics"

    # --- Pagination ---------------------------------------------------------
    default_page_size: int = 50
    max_page_size: int = 200

    @field_validator("jwt_secret")
    @classmethod
    def _jwt_secret_not_empty(cls, v: str) -> str:
        if not v or v in {"dev", "change_me_jwt_secret"}:
            raise ValueError(
                "JWT_SECRET must be set to a strong random value. "
                "Generate one with: openssl rand -hex 32"
            )
        return v

    @field_validator("anon_salt")
    @classmethod
    def _anon_salt_not_placeholder(cls, v: str) -> str:
        if not v or v in {"dev", "change_me_anon_salt", "change-me-anon-salt"}:
            raise ValueError(
                "ANON_SALT must be set to a strong random value. "
                "Generate one with: openssl rand -hex 32"
            )
        return v

    @field_validator("environment")
    @classmethod
    def _known_env(cls, v: str) -> str:
        allowed = {"development", "staging", "production"}
        if v not in allowed:
            raise ValueError(f"ENVIRONMENT must be one of {sorted(allowed)}")
        return v

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def allowed_host_list(self) -> list[str]:
        return [h.strip() for h in self.allowed_hosts.split(",") if h.strip()]

    @property
    def is_production(self) -> bool:
        return self.environment == "production"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
