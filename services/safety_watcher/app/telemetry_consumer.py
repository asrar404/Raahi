"""Redis Stream consumer.

Wraps stream mechanics so `watcher.py` deals in decoded telemetry rather than
Redis bytes.

Uses a **consumer group** rather than the simpler `XREAD` with a `$` cursor.
With `$`, a restarting watcher silently skips everything that arrived while it
was down — which for this service means missing the fixes that would have
triggered an SOS. A consumer group tracks acknowledgements server-side, so a
restart resumes exactly where it left off, and `XAUTOCLAIM` recovers entries
that were delivered but never acknowledged because the process died mid-fix.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, AsyncIterator, Optional

import redis.asyncio as aioredis
from redis.exceptions import ResponseError

from app.config import settings

log = logging.getLogger(__name__)


@dataclass
class TelemetryFix:
    """One decoded GPS fix from the stream."""

    entry_id: str
    trip_id: str
    user_id: str
    lat: float
    lon: float
    spent: float = 0.0
    contacts: list = None  # type: ignore[assignment]
    speed_kmh: Optional[float] = None
    battery_pct: Optional[int] = None

    def __post_init__(self) -> None:
        if self.contacts is None:
            self.contacts = []

    @property
    def is_valid(self) -> bool:
        return (
            bool(self.trip_id)
            and bool(self.user_id)
            and -90 <= self.lat <= 90
            and -180 <= self.lon <= 180
        )


def _decode(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value) if value is not None else ""


def parse_fix(entry_id: Any, fields: dict) -> Optional[TelemetryFix]:
    """Decode one stream entry. Returns None when unusable.

    Malformed entries are dropped rather than raised: one bad producer must not
    stall the stream for every other trip being monitored.
    """
    try:
        raw = {_decode(k): _decode(v) for k, v in fields.items()}

        contacts: list = []
        if raw.get("contacts"):
            try:
                parsed = json.loads(raw["contacts"])
                if isinstance(parsed, list):
                    contacts = parsed
            except json.JSONDecodeError:
                log.debug("Unparseable contacts field on entry %s", _decode(entry_id))

        fix = TelemetryFix(
            entry_id=_decode(entry_id),
            trip_id=raw.get("trip_id", ""),
            user_id=raw.get("user_id", ""),
            lat=float(raw["lat"]),
            lon=float(raw["lon"]),
            spent=float(raw.get("spent") or 0),
            contacts=contacts,
            speed_kmh=float(raw["speed"]) if raw.get("speed") else None,
            battery_pct=int(float(raw["battery"])) if raw.get("battery") else None,
        )
    except (KeyError, TypeError, ValueError) as exc:
        log.warning("Dropping malformed telemetry entry %s: %s", _decode(entry_id), exc)
        return None

    if not fix.is_valid:
        log.warning("Dropping invalid telemetry entry %s (trip=%r lat=%s lon=%s)",
                    fix.entry_id, fix.trip_id, fix.lat, fix.lon)
        return None

    return fix


class TelemetryConsumer:
    """Consumer-group reader over the telemetry stream."""

    def __init__(self) -> None:
        self.redis: Optional[aioredis.Redis] = None
        self.stream = settings.TELEMETRY_STREAM
        self.group = settings.CONSUMER_GROUP
        self.consumer = settings.CONSUMER_NAME
        self._recovered_backlog = False

    async def connect(self) -> aioredis.Redis:
        """Connect and ensure the consumer group exists."""
        self.redis = aioredis.from_url(
            settings.REDIS_URL,
            decode_responses=False,
            socket_connect_timeout=10,
            socket_keepalive=True,
        )
        await self.redis.ping()

        try:
            # mkstream so startup works before the gateway has produced anything
            await self.redis.xgroup_create(
                self.stream, self.group, id="0", mkstream=True
            )
            log.info("Created consumer group %r on %r", self.group, self.stream)
        except ResponseError as exc:
            if "BUSYGROUP" in str(exc):
                log.info("Consumer group %r already exists", self.group)
            else:
                raise

        log.info("Consuming %r as %r/%r", self.stream, self.group, self.consumer)
        return self.redis

    async def close(self) -> None:
        if self.redis is not None:
            await self.redis.close()
            self.redis = None

    async def _claim_stale(self) -> list[tuple[Any, dict]]:
        """Reclaim entries delivered to a dead consumer but never acknowledged.

        Covers the crash-mid-processing case: without this, those fixes stay
        pending forever and are never evaluated.
        """
        if self.redis is None:
            return []
        try:
            result = await self.redis.xautoclaim(
                self.stream, self.group, self.consumer,
                min_idle_time=60_000,  # 60s
                count=settings.BATCH_SIZE,
            )
            # redis-py returns (next_cursor, entries) or (next, entries, deleted)
            entries = result[1] if len(result) > 1 else []
            if entries:
                log.warning("Reclaimed %d unacknowledged telemetry entries", len(entries))
            return entries
        except ResponseError as exc:
            log.debug("XAUTOCLAIM unavailable (%s); needs Redis 6.2+", exc)
            return []
        except Exception as exc:  # noqa: BLE001
            log.warning("XAUTOCLAIM failed: %s", exc)
            return []

    async def read(self) -> AsyncIterator[TelemetryFix]:
        """Yield decoded fixes, blocking when the stream is idle.

        On the first pass, reads id="0" to drain anything already pending for
        this consumer, then switches to ">" for new entries only.
        """
        if self.redis is None:
            raise RuntimeError("Consumer not connected")

        # Recover our own backlog once, on first read
        cursor = "0" if not self._recovered_backlog else ">"

        for entry_id, fields in await self._claim_stale():
            fix = parse_fix(entry_id, fields)
            if fix is not None:
                yield fix
            else:
                await self.ack(_decode(entry_id))

        try:
            response = await self.redis.xreadgroup(
                groupname=self.group,
                consumername=self.consumer,
                streams={self.stream: cursor},
                count=settings.BATCH_SIZE,
                block=settings.POLL_INTERVAL_SECS * 1000,
            )
        except ResponseError as exc:
            # The stream can be trimmed away entirely; recreate the group.
            if "NOGROUP" in str(exc):
                log.warning("Consumer group vanished, recreating")
                await self.redis.xgroup_create(
                    self.stream, self.group, id="0", mkstream=True
                )
                return
            raise

        if not response:
            # Nothing pending on the "0" pass means the backlog is clear
            if cursor == "0":
                self._recovered_backlog = True
            return

        for _stream_name, entries in response:
            if cursor == "0" and not entries:
                self._recovered_backlog = True
            for entry_id, fields in entries:
                fix = parse_fix(entry_id, fields)
                if fix is None:
                    # Acknowledge junk so it is not redelivered forever
                    await self.ack(_decode(entry_id))
                    continue
                yield fix

        if cursor == "0":
            self._recovered_backlog = True

    async def ack(self, entry_id: str) -> None:
        """Acknowledge one entry as fully processed."""
        if self.redis is None:
            return
        try:
            await self.redis.xack(self.stream, self.group, entry_id)
        except Exception as exc:  # noqa: BLE001
            log.warning("XACK failed for %s: %s", entry_id, exc)

    async def stats(self) -> dict[str, Any]:
        if self.redis is None:
            return {"connected": False}
        try:
            length = await self.redis.xlen(self.stream)
            pending: dict[str, Any] = {}
            try:
                summary = await self.redis.xpending(self.stream, self.group)
                pending = {"count": summary.get("pending") if isinstance(summary, dict) else summary}
            except Exception:  # noqa: BLE001
                pending = {}
            return {
                "connected": True,
                "stream": self.stream,
                "length": int(length),
                "group": self.group,
                "consumer": self.consumer,
                "pending": pending,
            }
        except Exception as exc:  # noqa: BLE001
            return {"connected": False, "error": str(exc)}
