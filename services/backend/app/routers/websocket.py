"""Live trip WebSocket.

Carries GPS telemetry up and safety events down. For each fix received it:

  1. persists to `live_gps_telemetry` (durable, partitioned)
  2. publishes to the Redis telemetry stream (feeds safety_watcher)
  3. evaluates risk and route deviation in PostGIS
  4. broadcasts SOS_ALERT / RISK_UPDATE / OFF_ROUTE to subscribers

Three things this handler is careful about:

* **No dependency-injected DB connection.** `Depends(get_db)` would pin a
  pool slot for the socket's entire lifetime — hours, for a real trip. A
  handful of travellers would exhaust the pool. Connections are acquired per
  message instead.
* **Risk checks are throttled.** Persistence happens for every fix, but the
  expensive PostGIS evaluation is rate-limited per socket, so a buggy or
  hostile client cannot turn a chatty loop into a database DoS.
* **Events fire on transition, not on state.** OFF_ROUTE is emitted when the
  user *becomes* off-route, not on every subsequent fix, otherwise the phone
  is alerting continuously for the rest of the trip.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Optional

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect, status

from app.config import settings
from app.middleware.auth import _dev_auth_bypass_allowed, decode_token
from app.services import postgis, redis_bus
from app.services.db import acquire
from app.services.ws_manager import Event, manager

log = logging.getLogger(__name__)
router = APIRouter()

# Minimum seconds between full PostGIS risk evaluations per socket. The client
# reports every ~15s, so this only kicks in when a client misbehaves.
RISK_CHECK_MIN_INTERVAL = 5.0
# Drop a socket that has sent nothing at all for this long.
IDLE_TIMEOUT_SECONDS = 300.0
# Reject oversized frames rather than parsing them.
MAX_FRAME_BYTES = 16_384


class SocketState:
    """Per-connection state for transition detection and throttling."""

    def __init__(self) -> None:
        self.last_risk_check: float = 0.0
        self.was_off_route: bool = False
        self.was_in_risk: bool = False
        self.fixes_received: int = 0

    def should_check_risk(self) -> bool:
        now = time.monotonic()
        if now - self.last_risk_check < RISK_CHECK_MIN_INTERVAL:
            return False
        self.last_risk_check = now
        return True


async def _authorise(trip_id: str, token: Optional[str]) -> Optional[str]:
    """Confirm the caller may subscribe to this trip. Returns the user id.

    The token arrives as a query parameter because the React Native WebSocket
    API cannot set custom headers on the handshake.
    """
    async with acquire() as conn:
        trip = await conn.fetchrow(
            "SELECT id, user_id, status FROM trips WHERE id = $1", trip_id
        )
        if trip is None:
            return None

        if token:
            try:
                claims = decode_token(token)
            except Exception as exc:  # noqa: BLE001
                log.info("WS token rejected for trip %s: %s", trip_id, exc)
                return None
            supabase_uid = claims.get("sub")
            owner = await conn.fetchrow(
                """
                SELECT u.id FROM users u
                WHERE u.supabase_uid = $1 AND u.id = $2
                """,
                supabase_uid, trip["user_id"],
            )
            if owner is None:
                log.warning("WS token does not own trip %s", trip_id)
                return None
            return str(owner["id"])

        if _dev_auth_bypass_allowed():
            log.warning("DEV AUTH BYPASS: unauthenticated WS subscribe to trip %s", trip_id)
            return str(trip["user_id"])

        return None


async def _handle_telemetry(
    trip_id: str, user_id: str, data: dict[str, Any], state: SocketState, ws: WebSocket
) -> None:
    """Persist one fix, publish it, and react to what it implies."""
    try:
        lat = float(data["lat"])
        lon = float(data["lon"])
    except (KeyError, TypeError, ValueError):
        await manager.send_personal(ws, Event.ERROR, {
            "message": "TELEMETRY requires numeric 'lat' and 'lon'",
        })
        return

    if not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
        await manager.send_personal(ws, Event.ERROR, {
            "message": f"Coordinate out of range: {lat}, {lon}",
        })
        return

    def _opt_float(key: str) -> Optional[float]:
        value = data.get(key)
        try:
            return float(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    def _opt_int(key: str) -> Optional[int]:
        value = data.get(key)
        try:
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    speed = _opt_float("speed")
    accuracy = _opt_float("accuracy")
    battery = _opt_int("battery")
    state.fixes_received += 1

    # ── 1. Persist, then evaluate ───────────────────────────
    risk: Optional[dict[str, Any]] = None
    spent = 0.0
    contacts: list[dict] = []

    async with acquire() as conn:
        await postgis.insert_telemetry(
            conn, trip_id, user_id, lat, lon,
            accuracy_m=accuracy,
            speed_kmh=speed,
            heading_deg=_opt_float("heading"),
            altitude_m=_opt_float("altitude"),
            battery_pct=battery,
        )

        if state.should_check_risk():
            risk = await postgis.check_risk_and_deviation(conn, trip_id, lat, lon)

            # Pulled here so the watcher can escalate without calling back
            # into the gateway mid-emergency.
            row = await conn.fetchrow(
                """
                SELECT u.emergency_contacts,
                       COALESCE(t.total_actual_cost, 0)::DOUBLE PRECISION AS spent
                FROM trips t JOIN users u ON u.id = t.user_id
                WHERE t.id = $1
                """,
                trip_id,
            )
            if row is not None:
                contacts = row["emergency_contacts"] or []
                spent = float(row["spent"] or 0)

    # ── 2. Hand off to the safety watcher ───────────────────
    await redis_bus.publish_telemetry(
        trip_id=trip_id, user_id=user_id, lat=lat, lon=lon,
        spent=spent, contacts=contacts, speed_kmh=speed, battery_pct=battery,
    )

    if risk is None:
        # Throttled: acknowledge without a fresh assessment.
        await manager.send_personal(ws, Event.TELEMETRY_ACK, {
            "received": True, "throttled": True, "fixes": state.fixes_received,
        })
        return

    # ── 3. React ────────────────────────────────────────────
    in_risk = bool(risk["in_high_risk"])
    off_route = bool(risk["off_route"])

    await manager.send_risk_update(
        trip_id,
        risk_level=risk["max_risk"],
        in_risk_zone=in_risk,
        safety_score=risk.get("safety_score"),
        zones=risk.get("risk_zones", []),
    )

    # Only on entering a high-risk zone, so the phone is not alarming
    # continuously while the traveller crosses it.
    if in_risk and not state.was_in_risk:
        zone = (risk.get("risk_zones") or [{}])[0]
        log.warning(
            "Trip %s entered high-risk zone '%s' (risk=%s)",
            trip_id, zone.get("zone_name"), risk["max_risk"],
        )
        await manager.send_sos_alert(
            trip_id, {"lat": lat, "lon": lon}, risk,
            f"You are entering {zone.get('zone_name', 'a high-risk area')}. Stay alert.",
        )
    state.was_in_risk = in_risk

    if off_route and not state.was_off_route:
        log.info("Trip %s went off route", trip_id)
        await manager.send_off_route(trip_id, lat, lon)
    elif not off_route and state.was_off_route:
        await manager.send_back_on_route(trip_id)
    state.was_off_route = off_route

    await manager.send_personal(ws, Event.TELEMETRY_ACK, {
        "received": True,
        "in_high_risk": in_risk,
        "off_route": off_route,
        "safety_score": risk.get("safety_score"),
        "fixes": state.fixes_received,
    })


@router.websocket("/trip/{trip_id}")
async def trip_ws(
    ws: WebSocket,
    trip_id: str,
    token: Optional[str] = Query(default=None),
) -> None:
    """Subscribe to a trip's live safety channel and stream telemetry in.

    Client -> server:
        {"type": "TELEMETRY", "lat": .., "lon": .., "speed": .., "accuracy": ..}
        {"type": "PING"}

    Server -> client:
        {"event": "RISK_UPDATE" | "SOS_ALERT" | "OFF_ROUTE" | ..., "data": {..}}
    """
    user_id = await _authorise(trip_id, token)
    if user_id is None:
        # Close before accepting: no information about why is leaked.
        await ws.close(code=status.WS_1008_POLICY_VIOLATION, reason="Unauthorised")
        return

    await manager.connect(trip_id, ws)
    state = SocketState()

    try:
        while True:
            try:
                raw = await asyncio.wait_for(ws.receive_text(), timeout=IDLE_TIMEOUT_SECONDS)
            except asyncio.TimeoutError:
                log.info("WS idle timeout for trip %s after %ss", trip_id, IDLE_TIMEOUT_SECONDS)
                await ws.close(code=status.WS_1000_NORMAL_CLOSURE, reason="Idle timeout")
                break

            if len(raw) > MAX_FRAME_BYTES:
                await manager.send_personal(ws, Event.ERROR, {"message": "Frame too large"})
                continue

            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                await manager.send_personal(ws, Event.ERROR, {"message": "Malformed JSON"})
                continue

            if not isinstance(data, dict):
                await manager.send_personal(ws, Event.ERROR, {"message": "Expected a JSON object"})
                continue

            msg_type = data.get("type")

            if msg_type == "TELEMETRY":
                try:
                    await _handle_telemetry(trip_id, user_id, data, state, ws)
                except Exception as exc:  # noqa: BLE001
                    # One bad fix must not tear down a live trip's socket.
                    log.exception("Telemetry handling failed for trip %s: %s", trip_id, exc)
                    await manager.send_personal(ws, Event.ERROR, {
                        "message": "Could not process that location update",
                    })

            elif msg_type == "PING":
                await manager.send_personal(ws, "PONG", {"fixes": state.fixes_received})

            else:
                await manager.send_personal(ws, Event.ERROR, {
                    "message": f"Unknown message type: {msg_type!r}",
                })

    except WebSocketDisconnect:
        log.debug("WS client disconnected from trip %s", trip_id)
    except Exception as exc:  # noqa: BLE001
        log.exception("WS error on trip %s: %s", trip_id, exc)
    finally:
        await manager.disconnect(trip_id, ws)


@router.websocket("/watch/{trip_id}")
async def watch_ws(
    ws: WebSocket,
    trip_id: str,
    token: Optional[str] = Query(default=None),
) -> None:
    """Read-only subscription to a trip's safety events.

    For a guardian following someone's journey: they receive SOS_ALERT and
    RISK_UPDATE but cannot inject telemetry.
    """
    user_id = await _authorise(trip_id, token)
    if user_id is None:
        await ws.close(code=status.WS_1008_POLICY_VIOLATION, reason="Unauthorised")
        return

    await manager.connect(trip_id, ws)
    try:
        while True:
            # Ignore inbound content; this socket exists only to receive.
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    except Exception as exc:  # noqa: BLE001
        log.debug("Watch socket error on trip %s: %s", trip_id, exc)
    finally:
        await manager.disconnect(trip_id, ws)


@router.get("/stats")
async def ws_stats() -> dict[str, Any]:
    """Live connection counts, for debugging and dashboards."""
    return manager.stats()
