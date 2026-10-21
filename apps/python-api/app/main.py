"""FastAPI application factory.

Changes vs. the original scaffold:
  * explicit lifespan (DB init is no longer an import side-effect)
  * JWT-based auth on all mutating routes
  * Prometheus metrics endpoint (the monitoring stack already scrapes
    `/metrics` — the original app never exposed it, so that target was
    permanently `down`)
  * trusted-host middleware and a hardened CORS configuration
  * request-ID propagation for log correlation
"""

from __future__ import annotations

import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.gzip import GZipMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.config import settings
from app.db import init_db
from app.routers import assets, auth, content, usage

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("shop-api")

try:
    from prometheus_fastapi_instrumentator import Instrumentator
except ImportError:  # pragma: no cover - optional dependency
    Instrumentator = None  # type: ignore[assignment]


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(
        "starting %s v%s (env=%s)",
        settings.app_name,
        settings.app_version,
        settings.environment,
    )
    init_db()
    logger.info("database ready")
    yield
    logger.info("shutting down")


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description=(
        "API for the co-creation webshop: multi-modal content, co-authorship, "
        "usage telemetry for registered and anonymous visitors, and monetisation."
    ),
    lifespan=lifespan,
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
    redoc_url=None,
)

# --- Security headers & CORS ------------------------------------------------

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
    expose_headers=["X-Request-ID"],
    max_age=600,
)

if settings.allowed_host_list:
    app.add_middleware(
        TrustedHostMiddleware, allowed_hosts=settings.allowed_host_list
    )
else:
    logger.info(
        "ALLOWED_HOSTS empty -> TrustedHostMiddleware disabled. "
        "Set ALLOWED_HOSTS in production to defend against Host-header attacks."
    )

app.add_middleware(GZipMiddleware, minimum_size=1024)


@app.middleware("http")
async def request_context(request: Request, call_next):
    """Attach a request ID and emit timing/access logs."""
    request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex
    request.state.request_id = request_id
    started = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        logger.exception(
            "unhandled error request_id=%s path=%s", request_id, request.url.path
        )
        return JSONResponse(
            status_code=500,
            content={"detail": "Internal server error", "request_id": request_id},
        )
    elapsed_ms = (time.perf_counter() - started) * 1000
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Response-Time-ms"] = f"{elapsed_ms:.1f}"
    logger.info(
        "%s %s %s %.1fms request_id=%s",
        request.method,
        request.url.path,
        response.status_code,
        elapsed_ms,
        request_id,
    )
    return response


# --- Routers ----------------------------------------------------------------

app.include_router(auth.router)
app.include_router(content.router)
app.include_router(assets.router)
app.include_router(usage.router)


# --- Health & metrics -------------------------------------------------------

@app.get("/health", tags=["ops"])
def health() -> dict:
    """Liveness probe. Does not touch the database."""
    return {"ok": True, "service": "shop-api", "version": settings.app_version}


@app.get("/ready", tags=["ops"])
def readiness() -> dict:
    """Readiness probe. Verifies database connectivity."""
    from sqlalchemy import text

    from app.db import engine

    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:  # pragma: no cover - depends on infra
        logger.error("readiness check failed: %s", exc)
        return JSONResponse(status_code=503, content={"ok": False, "error": str(exc)})
    return {"ok": True, "database": "reachable"}


if settings.metrics_enabled and Instrumentator is not None:
    instrumentator = Instrumentator(
        should_group_status_codes=True,
        should_ignore_untemplated=True,
        # The in-flight gauge is OFF by default in the library. Without this
        # flag the `http_requests_inprogress` series is never registered at
        # all, and the `service:http_requests_in_flight` recording rule
        # silently returns an empty vector. Enabling it is cheap (one
        # gauge, one increment/decrement per request) and it is the only
        # way to see concurrent load on the API.
        should_instrument_requests_inprogress=True,
        excluded_handlers=["/metrics", "/health", "/ready"],
    )
    instrumentator.instrument(app)
    instrumentator.expose(
        app, endpoint=settings.metrics_path, include_in_schema=False
    )
    logger.info("prometheus metrics exposed at %s", settings.metrics_path)
elif settings.metrics_enabled:
    logger.warning(
        "prometheus-fastapi-instrumentator not installed; /metrics will not exist"
    )
