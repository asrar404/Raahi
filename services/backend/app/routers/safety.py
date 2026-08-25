"""Safety routes: SOS escalation, risk queries and crowdsourced reports.

The SOS path is the one that has to work when everything else is going wrong,
so it is built around three properties:

* **Idempotent.** A phone with a flaky connection will retry. Re-notifying
  emergency contacts every time would be actively harmful, so an unresolved
  SOS inside the dedupe window short-circuits.
* **Degrading.** Missing coordinates fall back to the last stored telemetry
  fix; a Twilio outage still broadcasts to WebSocket subscribers; a Redis
  outage still writes the audit row.
* **Bounded.** Notification is awaited so the client gets a real delivery
  count, but under a timeout so a hanging provider cannot wedge the request.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query, status

from app.config import settings
from app.dependencies import CurrentUser, DbConn, InternalOrUser, OptionalUser
from app.models.safety import (
    NotifyContactsRequest,
    NotifyContactsResponse,
    ReportCreate,
    ReportOut,
    ReportVote,
    RerouteBroadcast,
    RiskCheckResponse,
    SOSRequest,
    SOSResolveRequest,
    SOSResponse,
    ZoneOut,
)
from app.models.trip import LatLon
from app.services import postgis
from app.services.twilio_notifier import escalate
from app.services.ws_manager import manager

log = logging.getLogger(__name__)
router = APIRouter()

# Repeat SOS requests inside this window are treated as retries of the same
# incident rather than new ones.
SOS_DEDUPE_WINDOW_MINUTES = 10
# Ceiling on provider calls so an SOS response cannot hang on Twilio.
NOTIFY_TIMEOUT_SECONDS = 20.0


async def _resolve_location(
    conn, payload_lat: Optional[float], payload_lon: Optional[float], trip_id: Optional[str]
) -> Optional[LatLon]:
    """Use the supplied fix, else the trip's last known position.

    The SOS button is often pressed indoors or in a hurry, when the phone has
    no current fix. A slightly stale location beats none at all.
    """
    if payload_lat is not None and payload_lon is not None:
        return LatLon(lat=payload_lat, lon=payload_lon)

    if trip_id:
        last = await postgis.last_position(conn, trip_id)
        if last and last.get("lat") is not None:
            log.info("SOS for trip %s using last known fix from %s",
                     trip_id, last.get("recorded_at"))
            return LatLon(lat=last["lat"], lon=last["lon"])

    return None


# ============================================================
# SOS
# ============================================================
@router.post("/sos", response_model=SOSResponse)
async def trigger_sos(
    payload: SOSRequest,
    conn: DbConn,
    caller: InternalOrUser,
) -> SOSResponse:
    """Raise an SOS: flag the trip, alert subscribers, notify contacts.

    Accepts an internal service token so safety_watcher can escalate
    autonomously when its state machine detects a high-risk zone.
    """
    trip_id = str(payload.trip_id) if payload.trip_id else None

    # ── Identify the user ───────────────────────────────────
    user_row: Optional[dict[str, Any]] = caller
    if user_row is None:
        # Internal call: derive the user from the trip, or take it verbatim.
        if trip_id:
            user_row = await conn.fetchrow(
                """
                SELECT u.id, u.full_name, u.emergency_contacts, u.sos_enabled
                FROM users u JOIN trips t ON t.user_id = u.id
                WHERE t.id = $1
                """,
                trip_id,
            )
        elif payload.user_id:
            user_row = await conn.fetchrow(
                "SELECT id, full_name, emergency_contacts, sos_enabled FROM users WHERE id = $1",
                str(payload.user_id),
            )
        if user_row is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot resolve a user from trip_id or user_id",
            )
        user_row = dict(user_row)

    user_id = str(user_row["id"])
    user_name = user_row.get("full_name") or "A RAAHI traveller"
    contacts = user_row.get("emergency_contacts") or []
    sos_enabled = user_row.get("sos_enabled", True)

    # ── Dedupe ──────────────────────────────────────────────
    if trip_id:
        existing = await conn.fetchrow(
            """
            SELECT id, created_at FROM sos_events
            WHERE trip_id = $1
              AND resolved = FALSE
              AND created_at > NOW() - ($2 || ' minutes')::INTERVAL
            ORDER BY created_at DESC
            LIMIT 1
            """,
            trip_id, str(SOS_DEDUPE_WINDOW_MINUTES),
        )
        if existing is not None:
            log.info("SOS for trip %s deduped against event %s", trip_id, existing["id"])
            return SOSResponse(
                sos_event_id=existing["id"],
                trip_id=payload.trip_id,
                already_active=True,
                twilio_enabled=settings.twilio_configured,
            )

    # ── Locate ──────────────────────────────────────────────
    location = await _resolve_location(conn, payload.lat, payload.lon, trip_id)

    # ── Snapshot the risk picture ───────────────────────────
    risk_info: dict[str, Any] = dict(payload.risk_info or {})
    if location and trip_id and not risk_info:
        try:
            risk_info = await postgis.check_risk_and_deviation(
                conn, trip_id, location.lat, location.lon
            )
        except Exception as exc:  # noqa: BLE001 — never block an SOS on analytics
            log.error("Risk snapshot failed during SOS for trip %s: %s", trip_id, exc)

    refuges = risk_info.get("safe_refuges") or []
    if location and not refuges:
        try:
            refuges = await postgis.safe_refuges(
                conn, location.lat, location.lon,
                settings.REFUGE_SEARCH_RADIUS_M, settings.REFUGE_MAX_RISK,
            )
        except Exception as exc:  # noqa: BLE001
            log.error("Refuge lookup failed during SOS: %s", exc)

    zone_name = None
    zones = risk_info.get("risk_zones") or []
    if zones:
        zone_name = zones[0].get("zone_name")

    # ── Persist: trip flag + audit row ──────────────────────
    async with conn.transaction():
        if trip_id:
            await conn.execute(
                "UPDATE trips SET status = 'sos' WHERE id = $1 AND status <> 'completed'",
                trip_id,
            )
        event_row = await conn.fetchrow(
            """
            INSERT INTO sos_events
                (trip_id, user_id, trigger_source, location, risk_snapshot, contacts_alerted)
            VALUES
                ($1, $2, $3,
                 CASE WHEN $4::DOUBLE PRECISION IS NULL THEN NULL
                      ELSE ST_SetSRID(ST_MakePoint($4, $5), 4326) END,
                 $6, $7)
            RETURNING id
            """,
            trip_id, user_id, payload.trigger_source.value,
            location.lon if location else None,
            location.lat if location else None,
            risk_info, contacts,
        )
    sos_event_id = event_row["id"]

    # ── Fan out to live subscribers (fast path) ─────────────
    subscribers = 0
    if trip_id:
        subscribers = await manager.send_sos_alert(
            trip_id,
            location.model_dump() if location else {},
            {**risk_info, "safe_refuges": refuges},
            payload.message or "SOS triggered. Help is being notified.",
        )

    # ── Notify emergency contacts ───────────────────────────
    sms_sent = calls_placed = 0
    if payload.notify_contacts and sos_enabled and contacts and location:
        try:
            result = await asyncio.wait_for(
                escalate(contacts, user_name, location.lat, location.lon, zone_name),
                timeout=NOTIFY_TIMEOUT_SECONDS,
            )
            sms_sent = result["sms"].get("sent", 0)
            calls_placed = result["voice"].get("placed", 0)
        except asyncio.TimeoutError:
            log.error("SOS notification timed out after %ss (event %s)",
                      NOTIFY_TIMEOUT_SECONDS, sos_event_id)
        except Exception as exc:  # noqa: BLE001
            log.error("SOS notification failed (event %s): %s", sos_event_id, exc)
    elif not location:
        log.error("SOS event %s has no location — contacts not notified", sos_event_id)
    elif not sos_enabled:
        log.warning("User %s has SOS notifications disabled", user_id)
    elif not contacts:
        log.warning("User %s has no emergency contacts configured", user_id)

    await conn.execute(
        "UPDATE sos_events SET sms_sent = $2, calls_placed = $3 WHERE id = $1",
        sos_event_id, sms_sent, calls_placed,
    )

    log.warning(
        "SOS event %s raised (trip=%s user=%s subscribers=%d sms=%d calls=%d)",
        sos_event_id, trip_id, user_id, subscribers, sms_sent, calls_placed,
    )

    return SOSResponse(
        sos_event_id=sos_event_id,
        trip_id=payload.trip_id,
        location=location,
        subscribers_notified=subscribers,
        contacts_alerted=len(contacts),
        sms_sent=sms_sent,
        calls_placed=calls_placed,
        twilio_enabled=settings.twilio_configured,
        safe_refuges=refuges,
    )


@router.post("/sos/resolve")
async def resolve_sos(
    payload: SOSResolveRequest, user: CurrentUser, conn: DbConn
) -> dict[str, Any]:
    """Clear an active SOS and restore the trip status.

    Deliberately user-only — an automated system should never decide on its
    own that someone is safe again.
    """
    trip_id = str(payload.trip_id)
    owned = await conn.fetchrow(
        "SELECT id FROM trips WHERE id = $1 AND user_id = $2", trip_id, user["id"]
    )
    if owned is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Trip not found")

    async with conn.transaction():
        updated = await conn.execute(
            """
            UPDATE sos_events
            SET resolved = TRUE, resolved_at = NOW()
            WHERE trip_id = $1 AND resolved = FALSE
            """,
            trip_id,
        )
        await conn.execute(
            "UPDATE trips SET status = $2 WHERE id = $1 AND status = 'sos'",
            trip_id, payload.restore_status,
        )

    await manager.send_sos_resolved(trip_id)
    log.info("SOS resolved for trip %s (%s)", trip_id, updated)
    return {"trip_id": trip_id, "resolved": True, "status": payload.restore_status}


@router.post("/notify-contacts", response_model=NotifyContactsResponse)
async def notify_contacts(
    payload: NotifyContactsRequest, conn: DbConn, caller: InternalOrUser
) -> NotifyContactsResponse:
    """Send SMS/voice to emergency contacts.

    Split out from /sos so safety_watcher can notify without re-running the
    whole escalation, and so notification can be retried on its own.
    """
    contacts = payload.contacts
    user_name = payload.user_name

    if contacts is None or not user_name:
        user_row = None
        if caller is not None:
            user_row = caller
        elif payload.user_id:
            user_row = await conn.fetchrow(
                "SELECT full_name, emergency_contacts FROM users WHERE id = $1",
                str(payload.user_id),
            )
        elif payload.trip_id:
            user_row = await conn.fetchrow(
                """
                SELECT u.full_name, u.emergency_contacts
                FROM users u JOIN trips t ON t.user_id = u.id
                WHERE t.id = $1
                """,
                str(payload.trip_id),
            )
        if user_row is not None:
            user_row = dict(user_row)
            contacts = contacts if contacts is not None else (user_row.get("emergency_contacts") or [])
            user_name = user_name or user_row.get("full_name")

    if not contacts:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No emergency contacts available for this user",
        )

    result = await escalate(
        contacts, user_name or "A RAAHI traveller",
        payload.lat, payload.lon, payload.zone_name, voice=payload.voice,
    )

    return NotifyContactsResponse(
        contacts_alerted=result["contacts_alerted"],
        sms_sent=result["sms"].get("sent", 0),
        sms_failed=result["sms"].get("failed", 0),
        calls_placed=result["voice"].get("placed", 0),
        calls_failed=result["voice"].get("failed", 0),
        twilio_enabled=result["twilio_enabled"],
        dry_run=result["sms"].get("dry_run", False),
    )


# ============================================================
# Reroute fan-out
# ============================================================
@router.post("/reroute")
async def broadcast_reroute(
    payload: RerouteBroadcast, caller: InternalOrUser
) -> dict[str, Any]:
    """Push new route options to a trip's subscribers.

    Called by safety_watcher once the AI engine returns alternatives.
    """
    trip_id = str(payload.trip_id)
    delivered = await manager.send_reroute(trip_id, payload.new_routes, payload.trigger)
    log.info("Reroute (%s) broadcast to trip %s → %d subscribers",
             payload.trigger, trip_id, delivered)
    return {
        "trip_id": trip_id,
        "delivered": delivered,
        "routes": len(payload.new_routes),
        "trigger": payload.trigger,
    }


# ============================================================
# Risk reads
# ============================================================
@router.get("/risk", response_model=RiskCheckResponse)
async def check_risk(
    conn: DbConn,
    user: CurrentUser,
    lat: float = Query(ge=-90, le=90),
    lon: float = Query(ge=-180, le=180),
    trip_id: Optional[str] = Query(default=None),
    night_mode: Optional[bool] = Query(default=None),
) -> RiskCheckResponse:
    """Full risk assessment for a coordinate.

    trip_id is optional; without it, off_route is always false since there is
    no reference path to measure against.
    """
    if trip_id:
        owned = await conn.fetchrow(
            "SELECT id FROM trips WHERE id = $1 AND user_id = $2", trip_id, user["id"]
        )
        if owned is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Trip not found")

    # The all-zeros UUID never matches a trip, so fn_is_off_route returns
    # false rather than erroring when no trip context was supplied.
    result = await postgis.check_risk_and_deviation(
        conn,
        trip_id or "00000000-0000-0000-0000-000000000000",
        lat, lon, night_mode,
    )
    return RiskCheckResponse(**result)


@router.get("/alerts")
async def get_alerts(
    conn: DbConn,
    user: CurrentUser,
    lat: float = Query(ge=-90, le=90),
    lon: float = Query(ge=-180, le=180),
    radius_m: float = Query(default=300.0, gt=0, le=5000),
) -> dict[str, Any]:
    """Live crowdsourced reports near a point, for the map heatmap."""
    alerts = await postgis.nearby_alerts(conn, lat, lon, radius_m)
    return {"count": len(alerts), "radius_m": radius_m, "alerts": alerts}


@router.get("/refuges")
async def get_refuges(
    conn: DbConn,
    user: CurrentUser,
    lat: float = Query(ge=-90, le=90),
    lon: float = Query(ge=-180, le=180),
    radius_m: float = Query(default=600.0, gt=0, le=5000),
    max_risk: int = Query(default=2, ge=1, le=5),
) -> dict[str, Any]:
    """Nearest low-risk zones — "where can I go right now"."""
    refuges = await postgis.safe_refuges(conn, lat, lon, radius_m, max_risk)
    return {"count": len(refuges), "refuges": refuges}


@router.get("/zones", response_model=list[ZoneOut])
async def get_zones(
    conn: DbConn,
    user: CurrentUser,
    min_lat: float = Query(ge=-90, le=90),
    min_lon: float = Query(ge=-180, le=180),
    max_lat: float = Query(ge=-90, le=90),
    max_lon: float = Query(ge=-180, le=180),
    city: Optional[str] = Query(default=None),
) -> list[ZoneOut]:
    """Safety zones intersecting a map viewport, as GeoJSON polygons."""
    if min_lat >= max_lat or min_lon >= max_lon:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid bounding box: min values must be less than max values",
        )
    rows = await postgis.zones_in_bbox(conn, min_lat, min_lon, max_lat, max_lon, city)
    return [ZoneOut(**r) for r in rows]


# ============================================================
# Batch scoring (AI engine)
# ============================================================
@router.post("/score-points")
async def score_points(
    payload: dict, conn: DbConn, caller: InternalOrUser
) -> dict[str, Any]:
    """Batch safety scores for coordinates.

    The AI engine scores every leg midpoint of every candidate route — easily
    30+ points per plan — so this exists to make that one round trip instead
    of thirty.
    """
    raw_points = payload.get("points") or []
    if not raw_points:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="'points' must be a non-empty list"
        )
    if len(raw_points) > 200:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="At most 200 points per request"
        )

    try:
        pairs = [(float(p["lat"]), float(p["lon"])) for p in raw_points]
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Each point must be an object with numeric 'lat' and 'lon'",
        ) from exc

    night_mode = payload.get("night_mode")
    scores = await postgis.score_points(conn, pairs, night_mode)
    resolved_night = night_mode if night_mode is not None else await postgis.is_night(conn)

    return {
        "scores": scores,
        "night_mode": bool(resolved_night),
        "average": round(sum(scores) / len(scores), 3) if scores else 0.0,
    }


# ============================================================
# Crowdsourced reports
# ============================================================
@router.post("/report", response_model=ReportOut, status_code=status.HTTP_201_CREATED)
async def create_report(
    payload: ReportCreate, conn: DbConn, user: OptionalUser
) -> ReportOut:
    """File a hazard or safe-spot report.

    Authentication is optional on purpose: a bad token should never stop
    someone flagging a hazard that protects other travellers.
    """
    user_id = str(user["id"]) if user else None
    row = await postgis.insert_report(
        conn, user_id, payload.lat, payload.lon,
        payload.category.value, payload.description,
        payload.severity, payload.ttl_hours,
    )
    log.info("Report filed: %s severity=%d by user=%s",
             payload.category.value, payload.severity, user_id or "anonymous")
    return ReportOut(**row)


@router.post("/report/{report_id}/vote")
async def vote_on_report(
    report_id: str, payload: ReportVote, conn: DbConn, user: CurrentUser
) -> dict[str, Any]:
    """Up/downvote a report. Five downvotes suppress it from risk queries."""
    result = await postgis.vote_report(conn, report_id, payload.direction)
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Report not found")
    return {**result, "suppressed": result["downvotes"] >= 5}
