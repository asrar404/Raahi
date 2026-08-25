"""RAAHI API Gateway entrypoint.

Fronts the mobile app and owns the only PostGIS connection pool. The AI engine
and safety_watcher are separate services that call back in here with an
internal token.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app import __version__
from app.config import settings
from app.middleware.logging import RequestLoggingMiddleware, configure_logging
from app.routers import auth, budget, safety, trips, websocket
from app.services import redis_bus
from app.services.db import close_db, healthcheck as db_health, init_db
from app.services.ws_manager import manager

configure_logging(settings.LOG_LEVEL)
log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start and stop shared resources.

    PostgreSQL is required — failing to reach it is fatal, because every route
    depends on it and a half-alive gateway is worse than one that will not
    start. Redis is optional: without it, proactive monitoring by
    safety_watcher stops, but the gateway itself still works.
    """
    log.info("Starting %s v%s (env=%s)", settings.SERVICE_NAME, __version__, settings.ENVIRONMENT)

    await init_db()
    await redis_bus.init_redis()

    if not settings.auth_configured:
        log.warning(
            "SUPABASE_JWT_SECRET is not set. Authentication is disabled and "
            "requests fall back to the demo user. Do not run this in production."
        )
    if not settings.twilio_configured:
        log.warning(
            "Twilio is not configured (TWILIO_ENABLED=%s). SOS notifications "
            "will be logged instead of sent.", settings.TWILIO_ENABLED
        )
    if not settings.INTERNAL_API_KEY:
        log.warning(
            "INTERNAL_API_KEY is not set. Service-to-service endpoints are "
            "unauthenticated; set it before exposing this gateway."
        )

    try:
        yield
    finally:
        log.info("Shutting down %s", settings.SERVICE_NAME)
        await redis_bus.close_redis()
        await close_db()


app = FastAPI(
    title="RAAHI API Gateway",
    description=(
        "Safety-first, budget-aware travel companion for Indian urban transit. "
        "Handles auth, trips, budgets, live telemetry and SOS escalation."
    ),
    version=__version__,
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)

# ── Middleware ──────────────────────────────────────────────
# Order matters: CORS is added last so it runs outermost and still applies
# its headers to error responses raised further in.
app.add_middleware(RequestLoggingMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Request-ID", "X-Response-Time-ms"],
)


# ── Error handling ──────────────────────────────────────────
@app.exception_handler(RequestValidationError)
async def validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    """Return validation failures in a shape the mobile client can render."""
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={
            "detail": "Request validation failed",
            "errors": [
                {"field": ".".join(str(p) for p in e["loc"][1:]), "message": e["msg"]}
                for e in exc.errors()
            ],
            "request_id": getattr(request.state, "request_id", None),
        },
    )


@app.exception_handler(Exception)
async def unhandled_handler(request: Request, exc: Exception) -> JSONResponse:
    """Last-resort handler.

    Internal details are withheld in production; the request ID lets the same
    failure be found in the server logs.
    """
    request_id = getattr(request.state, "request_id", None)
    log.exception("Unhandled error [rid=%s] on %s %s", request_id, request.method, request.url.path)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "detail": "Internal server error" if settings.is_production else str(exc),
            "request_id": request_id,
        },
    )


# ── Routes ──────────────────────────────────────────────────
app.include_router(auth.router, prefix="/api/v1/auth", tags=["Auth"])
app.include_router(trips.router, prefix="/api/v1/trips", tags=["Trips"])
app.include_router(safety.router, prefix="/api/v1/safety", tags=["Safety"])
app.include_router(budget.router, prefix="/api/v1/budget", tags=["Budget"])
app.include_router(websocket.router, prefix="/ws", tags=["WebSocket"])


@app.get("/health", tags=["Meta"])
async def health() -> dict[str, Any]:
    """Detailed health: dependency status and live connection counts.

    Returns 200 even when a dependency is down; use /health/ready for the
    orchestrator's readiness gate.
    """
    database = await db_health()
    redis_status = await redis_bus.healthcheck()
    return {
        "status": "ok" if database.get("connected") else "degraded",
        "service": settings.SERVICE_NAME,
        "version": __version__,
        "environment": settings.ENVIRONMENT,
        "database": database,
        "redis": redis_status,
        "websockets": manager.stats(),
        "features": {
            "auth": settings.auth_configured,
            "twilio": settings.twilio_configured,
            "internal_api_key": bool(settings.INTERNAL_API_KEY),
        },
    }


@app.get("/health/live", tags=["Meta"])
async def liveness() -> dict[str, str]:
    """Process is running. Cheap enough to poll frequently."""
    return {"status": "alive"}


@app.get("/health/ready", tags=["Meta"])
async def readiness() -> JSONResponse:
    """Ready to serve traffic — requires a working database."""
    database = await db_health()
    ready = bool(database.get("connected"))
    return JSONResponse(
        status_code=status.HTTP_200_OK if ready else status.HTTP_503_SERVICE_UNAVAILABLE,
        content={"status": "ready" if ready else "not_ready", "database": database},
    )


@app.get("/", tags=["Meta"])
async def root() -> dict[str, Any]:
    return {
        "service": "RAAHI API Gateway",
        "version": __version__,
        "docs": "/docs",
        "health": "/health",
    }
