"""WebSocket connection registry and event fan-out.

One trip can have several live sockets — the traveller's phone plus any
guardian watching their progress — so connections are tracked per trip_id.

Two details worth keeping:

* Every socket gets its own send lock. `WebSocket.send_text` is not safe
  against concurrent callers, and a telemetry-driven RISK_UPDATE can easily
  overlap a watcher-driven SOS_ALERT, which would interleave frames and
  corrupt the stream.
* Broadcast collects failures and prunes them afterwards rather than
  mutating the connection set mid-iteration.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Set

from fastapi import WebSocket

log = logging.getLogger(__name__)


class Event:
    """Event names shared with the mobile client's useWebSocket hook."""

    SOS_ALERT = "SOS_ALERT"
    SOS_RESOLVED = "SOS_RESOLVED"
    REROUTE = "REROUTE"
    RISK_UPDATE = "RISK_UPDATE"
    OFF_ROUTE = "OFF_ROUTE"
    BACK_ON_ROUTE = "BACK_ON_ROUTE"
    BUDGET_ALERT = "BUDGET_ALERT"
    LEG_ADVANCED = "LEG_ADVANCED"
    TRIP_COMPLETED = "TRIP_COMPLETED"
    TELEMETRY_ACK = "TELEMETRY_ACK"
    ERROR = "ERROR"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ConnectionManager:
    def __init__(self) -> None:
        self.active: Dict[str, Set[WebSocket]] = {}
        self._send_locks: Dict[WebSocket, asyncio.Lock] = {}
        self._registry_lock = asyncio.Lock()

    # ── lifecycle ───────────────────────────────────────────
    async def connect(self, trip_id: str, ws: WebSocket) -> None:
        await ws.accept()
        async with self._registry_lock:
            self.active.setdefault(trip_id, set()).add(ws)
            self._send_locks[ws] = asyncio.Lock()
        log.info("WS connected trip=%s (subscribers=%d)", trip_id, self.count(trip_id))

    async def disconnect(self, trip_id: str, ws: WebSocket) -> None:
        async with self._registry_lock:
            self._remove(trip_id, ws)
        log.info("WS disconnected trip=%s (subscribers=%d)", trip_id, self.count(trip_id))

    def _remove(self, trip_id: str, ws: WebSocket) -> None:
        """Unsynchronised removal — callers must hold the registry lock."""
        peers = self.active.get(trip_id)
        if peers is not None:
            peers.discard(ws)
            if not peers:
                self.active.pop(trip_id, None)
        self._send_locks.pop(ws, None)

    def count(self, trip_id: str) -> int:
        return len(self.active.get(trip_id, ()))

    def stats(self) -> dict[str, Any]:
        return {
            "trips": len(self.active),
            "connections": sum(len(v) for v in self.active.values()),
        }

    # ── sending ─────────────────────────────────────────────
    async def _send(self, ws: WebSocket, message: str) -> bool:
        """Send under this socket's lock. False means the socket is dead."""
        lock = self._send_locks.get(ws)
        if lock is None:
            return False
        try:
            async with lock:
                await ws.send_text(message)
            return True
        except Exception as exc:  # noqa: BLE001 — any failure means drop it
            log.debug("WS send failed, dropping connection: %s", exc)
            return False

    async def send_personal(self, ws: WebSocket, event_type: str, payload: dict) -> bool:
        message = json.dumps({"event": event_type, "data": payload, "ts": _now()})
        return await self._send(ws, message)

    async def broadcast(self, trip_id: str, event_type: str, payload: dict) -> int:
        """Push an event to every subscriber of a trip. Returns delivery count."""
        targets = list(self.active.get(trip_id, ()))
        if not targets:
            log.debug("No subscribers for trip=%s, dropping %s", trip_id, event_type)
            return 0

        message = json.dumps({"event": event_type, "data": payload, "ts": _now()})
        results = await asyncio.gather(
            *(self._send(ws, message) for ws in targets),
            return_exceptions=True,
        )

        dead = [ws for ws, ok in zip(targets, results) if ok is not True]
        if dead:
            async with self._registry_lock:
                for ws in dead:
                    self._remove(trip_id, ws)

        delivered = len(targets) - len(dead)
        log.debug("Broadcast %s to trip=%s → %d/%d", event_type, trip_id, delivered, len(targets))
        return delivered

    # ── typed helpers ───────────────────────────────────────
    async def send_sos_alert(
        self,
        trip_id: str,
        location: dict,
        risk_info: dict,
        message: str = "High risk zone detected. SOS triggered.",
    ) -> int:
        return await self.broadcast(trip_id, Event.SOS_ALERT, {
            "location": location,
            "risk": risk_info,
            "refuges": risk_info.get("safe_refuges", []),
            "message": message,
        })

    async def send_sos_resolved(self, trip_id: str) -> int:
        return await self.broadcast(trip_id, Event.SOS_RESOLVED, {
            "message": "SOS cleared. Stay safe.",
        })

    async def send_reroute(self, trip_id: str, new_routes: list, trigger: str = "manual") -> int:
        return await self.broadcast(trip_id, Event.REROUTE, {
            "new_routes": new_routes,
            "trigger": trigger,
        })

    async def send_risk_update(
        self,
        trip_id: str,
        risk_level: int,
        in_risk_zone: bool,
        safety_score: float | None = None,
        zones: list | None = None,
    ) -> int:
        return await self.broadcast(trip_id, Event.RISK_UPDATE, {
            "risk_level": risk_level,
            "in_risk_zone": in_risk_zone,
            "safety_score": safety_score,
            "zones": zones or [],
        })

    async def send_off_route(self, trip_id: str, lat: float, lon: float, distance_m: float | None = None) -> int:
        return await self.broadcast(trip_id, Event.OFF_ROUTE, {
            "lat": lat,
            "lon": lon,
            "distance_m": distance_m,
            "message": "You are off your planned route.",
        })

    async def send_back_on_route(self, trip_id: str) -> int:
        return await self.broadcast(trip_id, Event.BACK_ON_ROUTE, {
            "message": "Back on route.",
        })

    async def send_budget_alert(self, trip_id: str, summary: dict) -> int:
        return await self.broadcast(trip_id, Event.BUDGET_ALERT, summary)

    async def send_trip_completed(self, trip_id: str, summary: dict) -> int:
        return await self.broadcast(trip_id, Event.TRIP_COMPLETED, summary)


# Process-wide singleton.
#
# Scaling past one gateway replica needs a Redis pub/sub bridge here so an
# event raised on instance A reaches a socket held by instance B. redis_bus
# already provides the transport; only the subscriber loop is missing.
manager = ConnectionManager()
