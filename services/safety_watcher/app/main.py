"""RAAHI Safety Watcher entrypoint.

A background consumer with an HTTP surface for health and introspection only.
The real work happens in the asyncio task started by the lifespan handler.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Any, Optional

from fastapi import FastAPI, status
from fastapi.responses import JSONResponse

from app import __version__
from app.config import settings
from app.db import close_pool, healthcheck as db_health, init_pool
from app.sos_pipeline import close_client, init_client
from app.watcher import SafetyWatcher

logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    force=True,
)
log = logging.getLogger(__name__)

watcher: Optional[SafetyWatcher] = None
_watcher_task: Optional[asyncio.Task] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start dependencies, then the consume loop as a background task.

    The database and HTTP client are initialised before the loop starts, so the
    first telemetry fix does not race an uninitialised pool.
    """
    global watcher, _watcher_task

    log.info("Starting %s v%s", settings.SERVICE_NAME, __version__)

    await init_pool()
    await init_client()

    if not settings.INTERNAL_API_KEY:
        log.warning(
            "INTERNAL_API_KEY is not set. Calls to the gateway will be "
            "unauthenticated and will fail if it requires the token."
        )

    watcher = SafetyWatcher()
    _watcher_task = asyncio.create_task(watcher.start(), name="safety-watcher")

    def _on_done(task: asyncio.Task) -> None:
        # Surface a crashed loop instead of letting it fail silently — a dead
        # watcher means nobody is monitoring live trips.
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            log.critical("Watcher task died: %s", exc, exc_info=exc)

    _watcher_task.add_done_callback(_on_done)

    try:
        yield
    finally:
        log.info("Shutting down %s", settings.SERVICE_NAME)
        if watcher is not None:
            await watcher.stop()
        if _watcher_task is not None:
            _watcher_task.cancel()
            try:
                await _watcher_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        await close_client()
        await close_pool()


app = FastAPI(
    title="RAAHI Safety Watcher",
    description=(
        "Consumes the telemetry stream and runs the per-trip safety state "
        "machine: risk-zone entry, route deviation, stalling and SOS escalation."
    ),
    version=__version__,
    lifespan=lifespan,
)


@app.get("/health")
async def health() -> dict[str, Any]:
    database = await db_health()
    stream = await watcher.consumer.stats() if watcher else {"connected": False}
    alive = bool(_watcher_task and not _watcher_task.done())

    return {
        "status": "ok" if (alive and database.get("connected")) else "degraded",
        "service": settings.SERVICE_NAME,
        "version": __version__,
        "watcher_alive": alive,
        "database": database,
        "stream": stream,
        "watched_trips": len(watcher.active_trips) if watcher else 0,
        "stats": dict(watcher.stats) if watcher else {},
        "thresholds": {
            "risk": settings.RISK_THRESHOLD,
            "off_route_m": settings.OFF_ROUTE_THRESHOLD_M,
            "off_route_strikes": settings.OFF_ROUTE_STRIKES,
            "stationary_secs": settings.STATIONARY_THRESHOLD_SECS,
        },
    }


@app.get("/health/live")
async def liveness() -> dict[str, str]:
    return {"status": "alive"}


@app.get("/health/ready")
async def readiness() -> JSONResponse:
    """Ready only when the database is reachable and the loop is running."""
    database = await db_health()
    alive = bool(_watcher_task and not _watcher_task.done())
    ready = alive and bool(database.get("connected"))
    return JSONResponse(
        status_code=status.HTTP_200_OK if ready else status.HTTP_503_SERVICE_UNAVAILABLE,
        content={
            "status": "ready" if ready else "not_ready",
            "watcher_alive": alive,
            "database": database,
        },
    )


@app.get("/trips")
async def watched_trips() -> dict[str, Any]:
    """In-memory state for every trip currently being watched. Debug aid."""
    if watcher is None:
        return {"running": False, "watched_trips": 0, "trips": []}
    return watcher.snapshot()


@app.get("/")
async def root() -> dict[str, Any]:
    return {
        "service": "RAAHI Safety Watcher",
        "version": __version__,
        "endpoints": ["/health", "/trips"],
    }
