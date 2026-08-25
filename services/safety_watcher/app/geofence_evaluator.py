"""Geofence and deviation evaluation.

Calls the same PL/pgSQL functions the gateway uses, so a location judged
high-risk by the WebSocket path is judged identically here. Duplicating the
risk logic in Python would let the two drift apart, and a disagreement about
whether someone is in danger is not an acceptable failure mode.

The state transitions below are ordered by severity: risk zone outranks
off-route, which outranks stationary. When several conditions hold at once the
most serious one wins.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from app.config import settings
from app.db import acquire
from app.state_machine import TripContext, TripState

log = logging.getLogger(__name__)


async def evaluate(ctx: TripContext, lat: float, lon: float) -> dict[str, Any]:
    """Assess one fix and advance the trip's state machine.

    Returns a dict describing what was found, including `state_changed` so the
    caller can act on transitions rather than on steady state.
    """
    async with acquire() as conn:
        night_mode = bool(await conn.fetchval("SELECT fn_is_night()"))

        # ── 1. Risk zones ───────────────────────────────────
        risk_rows = await conn.fetch(
            "SELECT * FROM fn_get_risk_zone($1, $2, $3, $4)",
            lat, lon, settings.RISK_THRESHOLD, night_mode,
        )
        risk_zones = [dict(r) for r in risk_rows]
        in_high_risk = bool(risk_zones)
        max_risk = int(risk_zones[0]["risk_score"]) if risk_zones else 0

        # ── 2. Route deviation ──────────────────────────────
        off_route = bool(await conn.fetchval(
            "SELECT fn_is_off_route($1, $2, $3, $4)",
            ctx.trip_id, lat, lon, settings.OFF_ROUTE_THRESHOLD_M,
        ))

        # ── 3. Safety score + refuges ───────────────────────
        safety_score = await conn.fetchval(
            "SELECT fn_point_safety_score($1, $2, $3)", lat, lon, night_mode
        )

        refuges: list[dict[str, Any]] = []
        if in_high_risk:
            refuge_rows = await conn.fetch(
                "SELECT * FROM fn_find_safe_refuges($1, $2, 800, 2)", lat, lon
            )
            refuges = [dict(r) for r in refuge_rows]

        # ── 4. Trip status ──────────────────────────────────
        # Read from the database rather than inferred: the traveller may have
        # completed or cancelled the trip in the app while fixes are still
        # in flight on the stream.
        trip_status = await conn.fetchval("SELECT status FROM trips WHERE id = $1", ctx.trip_id)

    # ── 5. Movement ─────────────────────────────────────────
    moved = ctx.update_position(lat, lon, settings.MOVEMENT_THRESHOLD_M)

    # ── 6. Advance the state machine ────────────────────────
    if trip_status in ("completed", "cancelled"):
        state_changed = ctx.transition(TripState.COMPLETED)

    elif in_high_risk:
        # Most serious condition — takes precedence over everything else.
        state_changed = ctx.transition(
            TripState.SOS_TRIGGERED if ctx.sos_triggered else TripState.HIGH_RISK_ZONE
        )

    elif off_route:
        ctx.off_route_count += 1
        if ctx.off_route_count >= settings.OFF_ROUTE_STRIKES:
            state_changed = ctx.transition(TripState.OFF_ROUTE)
        else:
            # Below the strike threshold this is probably GPS drift, not a
            # genuine deviation, so hold the current state.
            log.debug(
                "[%s] off-route strike %d/%d",
                ctx.trip_id, ctx.off_route_count, settings.OFF_ROUTE_STRIKES,
            )
            state_changed = False

    elif ctx.stationary_secs > settings.STATIONARY_THRESHOLD_SECS:
        ctx.off_route_count = 0
        state_changed = ctx.transition(TripState.STATIONARY)

    else:
        ctx.off_route_count = 0
        state_changed = ctx.transition(TripState.NAVIGATING)

    return {
        "state": ctx.state.name,
        "state_changed": state_changed,
        "in_high_risk": in_high_risk,
        "max_risk": max_risk,
        "risk_zones": risk_zones,
        "off_route": off_route,
        "off_route_strikes": ctx.off_route_count,
        "stationary_secs": ctx.stationary_secs,
        "moved": moved,
        "safety_score": float(safety_score) if safety_score is not None else None,
        "safe_refuges": refuges,
        "night_mode": night_mode,
        "trip_status": trip_status,
    }


async def load_trip_context(trip_id: str) -> Optional[dict[str, Any]]:
    """Fetch the user, contacts and spend for a trip.

    Used when the telemetry payload arrives without them — the gateway
    normally embeds contacts in the stream so an SOS needs no extra hop.
    """
    async with acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT t.id AS trip_id, t.status, t.user_id,
                   u.full_name, u.emergency_contacts, u.sos_enabled,
                   COALESCE(t.total_actual_cost, 0)::DOUBLE PRECISION AS spent
            FROM trips t JOIN users u ON u.id = t.user_id
            WHERE t.id = $1
            """,
            trip_id,
        )
    return dict(row) if row else None
