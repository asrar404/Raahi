"""Trip lifecycle routes.

A trip moves planned -> active -> completed, with cancelled and sos as side
exits. Legs advance in lockstep: exactly one leg is `in_progress` at a time,
which is what `fn_is_off_route` keys off when measuring deviation.

Geometry is never selected raw. Every read projects points through
ST_Y/ST_X and lines through ST_AsGeoJSON, so asyncpg returns numbers rather
than WKB blobs.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

import asyncpg
from fastapi import APIRouter, HTTPException, Query, status

from app.dependencies import CurrentUser, DbConn, InternalOrUser, Paginate
from app.models.trip import (
    LegStatusUpdate,
    TripCreate,
    TripLegOut,
    TripOut,
    TripStatus,
    TripStatusUpdate,
    TripSummary,
)
from app.services.ws_manager import manager

log = logging.getLogger(__name__)
router = APIRouter()

# NUMERIC columns are cast to DOUBLE PRECISION so asyncpg yields floats
# instead of Decimal, which keeps JSON serialisation straightforward.
TRIP_SELECT = """
    SELECT
        t.id, t.user_id, t.status,
        t.origin_name,
        ST_Y(t.origin_point)::DOUBLE PRECISION AS origin_lat,
        ST_X(t.origin_point)::DOUBLE PRECISION AS origin_lon,
        t.dest_name,
        ST_Y(t.dest_point)::DOUBLE PRECISION  AS dest_lat,
        ST_X(t.dest_point)::DOUBLE PRECISION  AS dest_lon,
        t.budget_ceiling::DOUBLE PRECISION     AS budget_ceiling,
        t.time_deadline,
        t.transit_prefs,
        t.total_planned_cost::DOUBLE PRECISION AS total_planned_cost,
        t.total_actual_cost::DOUBLE PRECISION  AS total_actual_cost,
        t.planned_eta, t.actual_eta,
        t.utility_score::DOUBLE PRECISION      AS utility_score,
        t.safety_priority, t.raw_intent, t.intent_json,
        t.started_at, t.completed_at, t.created_at
    FROM trips t
"""

LEG_SELECT = """
    SELECT
        l.id, l.trip_id, l.leg_order, l.mode,
        l.from_name,
        ST_Y(l.from_point)::DOUBLE PRECISION AS from_lat,
        ST_X(l.from_point)::DOUBLE PRECISION AS from_lon,
        l.to_name,
        ST_Y(l.to_point)::DOUBLE PRECISION   AS to_lat,
        ST_X(l.to_point)::DOUBLE PRECISION   AS to_lon,
        ST_AsGeoJSON(l.route_line)           AS route_geojson,
        l.distance_km::DOUBLE PRECISION      AS distance_km,
        l.planned_cost::DOUBLE PRECISION     AS planned_cost,
        l.actual_cost::DOUBLE PRECISION      AS actual_cost,
        l.planned_duration_mins, l.actual_duration_mins,
        l.provider, l.booking_ref, l.status,
        l.departed_at, l.arrived_at,
        l.safety_score::DOUBLE PRECISION     AS safety_score
    FROM trip_legs l
"""


def _leg_out(row: dict[str, Any]) -> TripLegOut:
    """Convert a leg row, flipping GeoJSON [lon,lat] to the app's [lat,lon]."""
    data = dict(row)
    geojson = data.pop("route_geojson", None)
    coords: Optional[list[list[float]]] = None
    if geojson:
        try:
            parsed = json.loads(geojson)
            coords = [[c[1], c[0]] for c in parsed.get("coordinates", [])]
        except (json.JSONDecodeError, IndexError, TypeError) as exc:
            log.warning("Unreadable route_line on leg %s: %s", data.get("id"), exc)
    data["route_coords"] = coords
    return TripLegOut(**data)


async def _fetch_trip(
    conn: asyncpg.Connection, trip_id: str, user_id: Optional[str] = None
) -> dict[str, Any]:
    """Load a trip, scoped to an owner when user_id is given.

    Returns 404 rather than 403 for another user's trip — acknowledging that
    an ID exists would leak someone's travel history.
    """
    if user_id is not None:
        row = await conn.fetchrow(
            f"{TRIP_SELECT} WHERE t.id = $1 AND t.user_id = $2", trip_id, user_id
        )
    else:
        row = await conn.fetchrow(f"{TRIP_SELECT} WHERE t.id = $1", trip_id)

    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Trip not found")
    return dict(row)


async def _fetch_legs(conn: asyncpg.Connection, trip_id: str) -> list[TripLegOut]:
    rows = await conn.fetch(f"{LEG_SELECT} WHERE l.trip_id = $1 ORDER BY l.leg_order", trip_id)
    return [_leg_out(dict(r)) for r in rows]


# ============================================================
# Create / read
# ============================================================
@router.post("", response_model=TripOut, status_code=status.HTTP_201_CREATED)
async def create_trip(payload: TripCreate, user: CurrentUser, conn: DbConn) -> TripOut:
    """Persist a selected route as a trip plus its legs.

    Runs in one transaction: a trip with a partial set of legs would make
    off-route detection measure against an incomplete path.
    """
    async with conn.transaction():
        trip_row = await conn.fetchrow(
            """
            INSERT INTO trips
                (user_id, origin_name, origin_point, dest_name, dest_point,
                 budget_ceiling, time_deadline, transit_prefs,
                 total_planned_cost, planned_eta, utility_score,
                 safety_priority, raw_intent, intent_json)
            VALUES
                ($1, $2, ST_SetSRID(ST_MakePoint($3, $4), 4326),
                 $5, ST_SetSRID(ST_MakePoint($6, $7), 4326),
                 $8, $9, $10, $11, $12, $13, $14, $15)
            RETURNING id
            """,
            user["id"],
            payload.origin_name, payload.origin_lon, payload.origin_lat,
            payload.dest_name, payload.dest_lon, payload.dest_lat,
            payload.budget_ceiling,
            payload.time_deadline,
            [m.value for m in payload.transit_prefs],
            payload.computed_planned_cost,
            payload.planned_eta,
            payload.utility_score,
            payload.safety_priority,
            payload.raw_intent,
            payload.intent_json,
        )
        trip_id = trip_row["id"]

        for leg in sorted(payload.legs, key=lambda x: x.leg_order):
            await conn.execute(
                """
                INSERT INTO trip_legs
                    (trip_id, leg_order, mode, from_name, from_point,
                     to_name, to_point, route_line, distance_km,
                     planned_cost, planned_duration_mins, provider, safety_score)
                VALUES
                    ($1, $2, $3, $4, ST_SetSRID(ST_MakePoint($5, $6), 4326),
                     $7, ST_SetSRID(ST_MakePoint($8, $9), 4326),
                     ST_GeomFromText($10, 4326), $11, $12, $13, $14, $15)
                """,
                trip_id, leg.leg_order, leg.mode.value,
                leg.from_name, leg.from_lon, leg.from_lat,
                leg.to_name, leg.to_lon, leg.to_lat,
                leg.line_wkt(),
                leg.distance_km, leg.planned_cost,
                leg.planned_duration_mins, leg.provider, leg.safety_score,
            )

    log.info("Created trip %s for user %s with %d legs",
             trip_id, user["id"], len(payload.legs))

    trip = await _fetch_trip(conn, str(trip_id), str(user["id"]))
    trip["legs"] = await _fetch_legs(conn, str(trip_id))
    return TripOut(**trip)


@router.get("", response_model=list[TripSummary])
async def list_trips(
    user: CurrentUser,
    conn: DbConn,
    page: Paginate,
    trip_status: Optional[TripStatus] = Query(default=None, alias="status"),
) -> list[TripSummary]:
    """Trip history, newest first."""
    rows = await conn.fetch(
        """
        SELECT
            t.id, t.status, t.origin_name, t.dest_name,
            t.budget_ceiling::DOUBLE PRECISION     AS budget_ceiling,
            t.total_planned_cost::DOUBLE PRECISION AS total_planned_cost,
            t.total_actual_cost::DOUBLE PRECISION  AS total_actual_cost,
            t.planned_eta, t.started_at, t.completed_at, t.created_at
        FROM trips t
        WHERE t.user_id = $1
          AND ($2::TEXT IS NULL OR t.status = $2)
        ORDER BY t.created_at DESC
        LIMIT $3 OFFSET $4
        """,
        user["id"],
        trip_status.value if trip_status else None,
        page.limit, page.offset,
    )
    return [TripSummary(**dict(r)) for r in rows]


@router.get("/active", response_model=Optional[TripOut])
async def active_trip(user: CurrentUser, conn: DbConn) -> Optional[TripOut]:
    """The user's in-flight trip, if any.

    Lets the app restore live navigation after a cold start without the user
    having to re-plan.
    """
    row = await conn.fetchrow(
        f"""
        {TRIP_SELECT}
        WHERE t.user_id = $1 AND t.status IN ('active', 'sos')
        ORDER BY t.started_at DESC NULLS LAST
        LIMIT 1
        """,
        user["id"],
    )
    if row is None:
        return None
    trip = dict(row)
    trip["legs"] = await _fetch_legs(conn, str(trip["id"]))
    return TripOut(**trip)


@router.get("/{trip_id}", response_model=TripOut)
async def get_trip(trip_id: str, user: CurrentUser, conn: DbConn) -> TripOut:
    """One trip with all its legs."""
    trip = await _fetch_trip(conn, trip_id, str(user["id"]))
    trip["legs"] = await _fetch_legs(conn, trip_id)
    return TripOut(**trip)


# ============================================================
# Intent (consumed by safety_watcher -> ai_engine on reroute)
# ============================================================
@router.get("/{trip_id}/intent")
async def get_trip_intent(trip_id: str, conn: DbConn, caller: InternalOrUser) -> dict[str, Any]:
    """Return the trip's ParsedIntent for replanning.

    Reachable with an internal service token because safety_watcher calls it
    while reacting to telemetry, with no user JWT in hand.

    When intent_json is empty — a trip created without going through the AI
    planner — synthesise an equivalent intent from the trip row. Reroute must
    never fail just because the original plan was hand-built.
    """
    user_id = str(caller["id"]) if caller else None
    trip = await _fetch_trip(conn, trip_id, user_id)

    stored = trip.get("intent_json") or {}
    if stored and stored.get("destination_raw"):
        return stored

    return {
        "source_raw": trip["origin_name"],
        "source_lat": trip["origin_lat"],
        "source_lon": trip["origin_lon"],
        "destination_raw": trip["dest_name"],
        "dest_lat": trip["dest_lat"],
        "dest_lon": trip["dest_lon"],
        "budget_ceiling": float(trip["budget_ceiling"]),
        "time_deadline": trip["time_deadline"].isoformat() if trip["time_deadline"] else None,
        "preferred_modes": list(trip["transit_prefs"] or ["metro", "bus"]),
        "safety_priority": bool(trip["safety_priority"]),
        "confidence": 1.0,
    }


# ============================================================
# Lifecycle transitions
# ============================================================
@router.post("/{trip_id}/start", response_model=TripOut)
async def start_trip(trip_id: str, user: CurrentUser, conn: DbConn) -> TripOut:
    """Mark a trip active and put its first leg in progress.

    Setting leg 0 to `in_progress` is what arms off-route detection —
    `fn_is_off_route` only measures against in-progress legs.
    """
    trip = await _fetch_trip(conn, trip_id, str(user["id"]))

    if trip["status"] == "active":
        trip["legs"] = await _fetch_legs(conn, trip_id)
        return TripOut(**trip)

    if trip["status"] not in ("planned",):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot start a trip with status '{trip['status']}'",
        )

    async with conn.transaction():
        await conn.execute(
            """
            UPDATE trips
            SET status = 'active', started_at = COALESCE(started_at, NOW())
            WHERE id = $1
            """,
            trip_id,
        )
        await conn.execute(
            """
            UPDATE trip_legs
            SET status = 'in_progress', departed_at = NOW()
            WHERE trip_id = $1
              AND leg_order = (
                  SELECT MIN(leg_order) FROM trip_legs WHERE trip_id = $1
              )
            """,
            trip_id,
        )

    log.info("Trip %s started", trip_id)
    trip = await _fetch_trip(conn, trip_id, str(user["id"]))
    trip["legs"] = await _fetch_legs(conn, trip_id)
    return TripOut(**trip)


@router.patch("/{trip_id}/status", response_model=TripOut)
async def update_status(
    trip_id: str, payload: TripStatusUpdate, user: CurrentUser, conn: DbConn
) -> TripOut:
    """Set the trip status directly, stamping timestamps to match."""
    await _fetch_trip(conn, trip_id, str(user["id"]))

    await conn.execute(
        """
        UPDATE trips
        SET status       = $2,
            started_at   = CASE WHEN $2 = 'active'
                                THEN COALESCE(started_at, NOW()) ELSE started_at END,
            completed_at = CASE WHEN $2 IN ('completed', 'cancelled')
                                THEN COALESCE(completed_at, NOW()) ELSE completed_at END,
            actual_eta   = CASE WHEN $2 = 'completed'
                                THEN COALESCE(actual_eta, NOW()) ELSE actual_eta END
        WHERE id = $1
        """,
        trip_id, payload.status.value,
    )

    log.info("Trip %s status -> %s", trip_id, payload.status.value)
    trip = await _fetch_trip(conn, trip_id, str(user["id"]))
    trip["legs"] = await _fetch_legs(conn, trip_id)
    return TripOut(**trip)


@router.post("/{trip_id}/advance-leg", response_model=TripOut)
async def advance_leg(trip_id: str, user: CurrentUser, conn: DbConn) -> TripOut:
    """Complete the current leg and begin the next one.

    Completes the trip automatically when the last leg finishes, so the client
    does not have to detect the end of the route itself.
    """
    await _fetch_trip(conn, trip_id, str(user["id"]))

    current = await conn.fetchrow(
        """
        SELECT id, leg_order, departed_at
        FROM trip_legs
        WHERE trip_id = $1 AND status = 'in_progress'
        ORDER BY leg_order
        LIMIT 1
        """,
        trip_id,
    )
    if current is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="No leg is currently in progress",
        )

    async with conn.transaction():
        await conn.execute(
            """
            UPDATE trip_legs
            SET status = 'completed',
                arrived_at = NOW(),
                actual_duration_mins = COALESCE(
                    actual_duration_mins,
                    GREATEST(0, EXTRACT(EPOCH FROM (NOW() - departed_at)) / 60)::INT
                )
            WHERE id = $1
            """,
            current["id"],
        )

        next_leg = await conn.fetchrow(
            """
            UPDATE trip_legs
            SET status = 'in_progress', departed_at = NOW()
            WHERE id = (
                SELECT id FROM trip_legs
                WHERE trip_id = $1 AND leg_order > $2 AND status = 'pending'
                ORDER BY leg_order
                LIMIT 1
            )
            RETURNING id, leg_order
            """,
            trip_id, current["leg_order"],
        )

        if next_leg is None:
            await conn.execute(
                """
                UPDATE trips
                SET status = 'completed',
                    completed_at = COALESCE(completed_at, NOW()),
                    actual_eta   = COALESCE(actual_eta, NOW())
                WHERE id = $1 AND status <> 'sos'
                """,
                trip_id,
            )

    trip = await _fetch_trip(conn, trip_id, str(user["id"]))
    trip["legs"] = await _fetch_legs(conn, trip_id)

    if next_leg is None:
        await manager.send_trip_completed(trip_id, {
            "trip_id": trip_id,
            "total_actual_cost": trip["total_actual_cost"],
        })
        log.info("Trip %s completed (final leg done)", trip_id)
    else:
        await manager.broadcast(trip_id, "LEG_ADVANCED", {
            "trip_id": trip_id,
            "leg_order": next_leg["leg_order"],
        })

    return TripOut(**trip)


@router.patch("/{trip_id}/legs/{leg_order}", response_model=TripLegOut)
async def update_leg(
    trip_id: str,
    leg_order: int,
    payload: LegStatusUpdate,
    user: CurrentUser,
    conn: DbConn,
) -> TripLegOut:
    """Update one leg's status and actuals."""
    await _fetch_trip(conn, trip_id, str(user["id"]))

    row = await conn.fetchrow(
        """
        UPDATE trip_legs
        SET status               = $3,
            actual_cost          = COALESCE($4, actual_cost),
            actual_duration_mins = COALESCE($5, actual_duration_mins),
            departed_at          = CASE WHEN $3 = 'in_progress'
                                        THEN COALESCE(departed_at, NOW()) ELSE departed_at END,
            arrived_at           = CASE WHEN $3 = 'completed'
                                        THEN COALESCE(arrived_at, NOW()) ELSE arrived_at END
        WHERE trip_id = $1 AND leg_order = $2
        RETURNING id
        """,
        trip_id, leg_order, payload.status.value,
        payload.actual_cost, payload.actual_duration_mins,
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Leg not found")

    leg_row = await conn.fetchrow(f"{LEG_SELECT} WHERE l.id = $1", row["id"])
    return _leg_out(dict(leg_row))


@router.post("/{trip_id}/complete", response_model=TripOut)
async def complete_trip(trip_id: str, user: CurrentUser, conn: DbConn) -> TripOut:
    """Finish a trip and close out any legs still open."""
    await _fetch_trip(conn, trip_id, str(user["id"]))

    async with conn.transaction():
        await conn.execute(
            """
            UPDATE trip_legs
            SET status = 'completed', arrived_at = COALESCE(arrived_at, NOW())
            WHERE trip_id = $1 AND status = 'in_progress'
            """,
            trip_id,
        )
        await conn.execute(
            """
            UPDATE trips
            SET status = 'completed',
                completed_at = COALESCE(completed_at, NOW()),
                actual_eta   = COALESCE(actual_eta, NOW())
            WHERE id = $1
            """,
            trip_id,
        )

    trip = await _fetch_trip(conn, trip_id, str(user["id"]))
    trip["legs"] = await _fetch_legs(conn, trip_id)
    await manager.send_trip_completed(trip_id, {
        "trip_id": trip_id,
        "total_actual_cost": trip["total_actual_cost"],
    })
    log.info("Trip %s completed", trip_id)
    return TripOut(**trip)


@router.delete("/{trip_id}", status_code=status.HTTP_200_OK)
async def cancel_trip(trip_id: str, user: CurrentUser, conn: DbConn) -> dict[str, Any]:
    """Cancel a trip.

    Soft cancel, not a delete: telemetry and expense history stay intact for
    the user's own records and for post-incident review.
    """
    await _fetch_trip(conn, trip_id, str(user["id"]))
    await conn.execute(
        """
        UPDATE trips
        SET status = 'cancelled', completed_at = COALESCE(completed_at, NOW())
        WHERE id = $1
        """,
        trip_id,
    )
    log.info("Trip %s cancelled", trip_id)
    return {"trip_id": trip_id, "status": "cancelled"}
