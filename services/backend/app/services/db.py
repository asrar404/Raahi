"""asyncpg connection pool for PostGIS.

Deliberately not SQLAlchemy. Everything valuable in this schema is raw
PostGIS (`ST_Contains`, `ST_DWithin`) wrapped in PL/pgSQL functions, so an
ORM would only add a translation layer to argue with. Pydantic already
covers the validation an ORM would otherwise give us.

Geometry columns are never selected raw — always project them through
`ST_Y()/ST_X()` so asyncpg hands back floats instead of WKB blobs.
"""

from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Optional

import asyncpg

from app.config import settings

log = logging.getLogger(__name__)

_pool: Optional[asyncpg.Pool] = None


async def _init_connection(conn: asyncpg.Connection) -> None:
    """Per-connection setup applied to every pooled connection.

    asyncpg returns json/jsonb as raw strings by default; register codecs so
    `emergency_contacts` and `intent_json` arrive as Python objects.
    """
    for typename in ("json", "jsonb"):
        await conn.set_type_codec(
            typename,
            encoder=json.dumps,
            decoder=json.loads,
            schema="pg_catalog",
        )


async def init_db() -> asyncpg.Pool:
    """Create the pool and fail fast if the database is not usable.

    Called from the FastAPI lifespan. Verifies connectivity, confirms PostGIS
    is present, and makes sure this month's telemetry partition exists so the
    hot write path never lands in the DEFAULT partition.
    """
    global _pool

    if _pool is not None:
        return _pool

    log.info("Connecting to PostgreSQL pool (min=%s max=%s)",
             settings.DB_POOL_MIN, settings.DB_POOL_MAX)

    _pool = await asyncpg.create_pool(
        dsn=settings.DATABASE_URL,
        min_size=settings.DB_POOL_MIN,
        max_size=settings.DB_POOL_MAX,
        command_timeout=settings.DB_COMMAND_TIMEOUT,
        init=_init_connection,
    )

    async with _pool.acquire() as conn:
        await conn.fetchval("SELECT 1")

        postgis_version = await conn.fetchval(
            "SELECT extversion FROM pg_extension WHERE extname = 'postgis'"
        )
        if not postgis_version:
            raise RuntimeError(
                "PostGIS extension missing. Run 01_extensions.sql before starting."
            )
        log.info("PostGIS %s ready", postgis_version)

        # Idempotent; safe on every boot.
        try:
            result = await conn.fetchval("SELECT fn_ensure_telemetry_partition(CURRENT_DATE)")
            log.info("Telemetry partition: %s", result)
        except asyncpg.PostgresError as exc:
            # Never block startup on housekeeping — the DEFAULT partition
            # will absorb writes until this is fixed.
            log.warning("Could not ensure telemetry partition: %s", exc)

    return _pool


async def close_db() -> None:
    """Drain and dispose of the pool on shutdown."""
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
        log.info("PostgreSQL pool closed")


def get_pool() -> asyncpg.Pool:
    """Return the live pool, or raise if the app never completed startup."""
    if _pool is None:
        raise RuntimeError("Database pool not initialised — did lifespan run?")
    return _pool


async def get_db() -> AsyncIterator[asyncpg.Connection]:
    """FastAPI dependency yielding a pooled connection for one request.

    Do not use this for WebSockets: a dependency-injected connection is held
    for the whole socket lifetime, and a multi-hour trip would pin a pool slot
    the entire time. Use `acquire()` per message instead.
    """
    pool = get_pool()
    async with pool.acquire() as conn:
        yield conn


@asynccontextmanager
async def acquire() -> AsyncIterator[asyncpg.Connection]:
    """Short-lived connection for background tasks and WebSocket handlers."""
    pool = get_pool()
    async with pool.acquire() as conn:
        yield conn


async def healthcheck() -> dict[str, Any]:
    """Pool stats plus a live round trip, for /health."""
    try:
        pool = get_pool()
    except RuntimeError as exc:
        return {"connected": False, "error": str(exc)}

    try:
        async with pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
        return {
            "connected": True,
            "size": pool.get_size(),
            "idle": pool.get_idle_size(),
            "max": pool.get_max_size(),
        }
    except Exception as exc:  # noqa: BLE001 — health must never raise
        return {"connected": False, "error": str(exc)}
