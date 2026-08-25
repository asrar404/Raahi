"""asyncpg pool for the safety watcher.

The obvious implementation opens a fresh `asyncpg.connect()` inside the
evaluator and closes it again per telemetry fix. With N active trips each
reporting every 15 seconds that is a new TCP connection, TLS handshake and
Postgres authentication several times a second — which is both the dominant
latency in the safety path and a reliable way to exhaust `max_connections`
under load. A pool removes all of it.
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
    for typename in ("json", "jsonb"):
        await conn.set_type_codec(
            typename, encoder=json.dumps, decoder=json.loads, schema="pg_catalog"
        )


async def init_pool() -> asyncpg.Pool:
    global _pool
    if _pool is not None:
        return _pool

    _pool = await asyncpg.create_pool(
        dsn=settings.DATABASE_URL,
        min_size=settings.DB_POOL_MIN,
        max_size=settings.DB_POOL_MAX,
        init=_init_connection,
    )
    async with _pool.acquire() as conn:
        await conn.fetchval("SELECT 1")
    log.info("PostgreSQL pool ready (min=%s max=%s)",
             settings.DB_POOL_MIN, settings.DB_POOL_MAX)
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
        log.info("PostgreSQL pool closed")


@asynccontextmanager
async def acquire() -> AsyncIterator[asyncpg.Connection]:
    if _pool is None:
        raise RuntimeError("Database pool not initialised")
    async with _pool.acquire() as conn:
        yield conn


async def healthcheck() -> dict[str, Any]:
    if _pool is None:
        return {"connected": False}
    try:
        async with _pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
        return {"connected": True, "size": _pool.get_size(), "idle": _pool.get_idle_size()}
    except Exception as exc:  # noqa: BLE001
        return {"connected": False, "error": str(exc)}
