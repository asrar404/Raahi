"""Redis bus between the gateway and the safety_watcher.

The gateway owns the WebSocket, so it is the only service that sees live
telemetry. The watcher runs the stateful safety machine (stationary
detection, off-route strike counts, SOS de-duplication) and must therefore
see every fix too. A Redis Stream connects them: the gateway XADDs each fix,
the watcher XREADs it.

A Stream rather than pub/sub, because the watcher's state machine cannot
silently miss fixes if it restarts — streams retain history, pub/sub does not.

Publishing is best-effort. The fix is already durably in PostGIS by the time
we get here, so a Redis outage degrades proactive monitoring but must never
fail the traveller's telemetry upload.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

import redis.asyncio as aioredis

from app.config import settings

log = logging.getLogger(__name__)

_redis: Optional[aioredis.Redis] = None


async def init_redis() -> Optional[aioredis.Redis]:
    """Connect to Redis. Returns None if unreachable — never raises.

    Startup is intentionally tolerant: the gateway is still fully functional
    for planning, trips and budgets without Redis.
    """
    global _redis
    if _redis is not None:
        return _redis

    try:
        client = aioredis.from_url(
            settings.REDIS_URL,
            encoding="utf-8",
            decode_responses=False,  # watcher reads raw bytes
            socket_connect_timeout=5,
            socket_keepalive=True,
        )
        await client.ping()
        _redis = client
        log.info("Redis connected at %s", settings.REDIS_URL)
    except Exception as exc:  # noqa: BLE001
        log.warning("Redis unavailable (%s) — safety_watcher will not receive telemetry", exc)
        _redis = None

    return _redis


async def close_redis() -> None:
    global _redis
    if _redis is not None:
        await _redis.close()
        _redis = None
        log.info("Redis connection closed")


def get_redis() -> Optional[aioredis.Redis]:
    return _redis


async def publish_telemetry(
    trip_id: str,
    user_id: str,
    lat: float,
    lon: float,
    spent: float = 0.0,
    contacts: Optional[list[dict]] = None,
    speed_kmh: Optional[float] = None,
    battery_pct: Optional[int] = None,
) -> Optional[str]:
    """XADD one fix onto the telemetry stream.

    Contacts ride along so the watcher can fire SOS notifications without a
    round trip back to the gateway during an emergency — every saved hop
    matters when someone is in a high-risk zone.

    Returns the stream entry ID, or None when Redis is unavailable.
    """
    client = _redis
    if client is None:
        return None

    fields: dict[str, Any] = {
        "trip_id": trip_id,
        "user_id": user_id,
        "lat": str(lat),
        "lon": str(lon),
        "spent": str(spent),
        "contacts": json.dumps(contacts or []),
    }
    if speed_kmh is not None:
        fields["speed"] = str(speed_kmh)
    if battery_pct is not None:
        fields["battery"] = str(battery_pct)

    try:
        entry_id = await client.xadd(
            settings.TELEMETRY_STREAM,
            fields,
            maxlen=settings.TELEMETRY_STREAM_MAXLEN,
            approximate=True,  # cheap trimming; exact length does not matter
        )
        return entry_id.decode() if isinstance(entry_id, bytes) else str(entry_id)
    except Exception as exc:  # noqa: BLE001
        log.warning("Failed to publish telemetry for trip=%s: %s", trip_id, exc)
        return None


async def publish_event(channel: str, payload: dict) -> bool:
    """Fire-and-forget pub/sub publish.

    Reserved for cross-replica WebSocket fan-out: with more than one gateway
    instance, an event raised on instance A needs to reach sockets held by
    instance B. Unused while running single-replica.
    """
    client = _redis
    if client is None:
        return False
    try:
        await client.publish(channel, json.dumps(payload))
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("Failed to publish on %s: %s", channel, exc)
        return False


async def healthcheck() -> dict[str, Any]:
    if _redis is None:
        return {"connected": False}
    try:
        await _redis.ping()
        length = await _redis.xlen(settings.TELEMETRY_STREAM)
        return {
            "connected": True,
            "stream": settings.TELEMETRY_STREAM,
            "stream_length": int(length),
        }
    except Exception as exc:  # noqa: BLE001
        return {"connected": False, "error": str(exc)}
