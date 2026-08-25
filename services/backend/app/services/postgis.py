"""PostGIS query layer.

Thin async wrappers over the PL/pgSQL functions in
`infrastructure/docker/postgres/init/04_functions.sql`. Keeping the SQL in the
database (rather than string-building it here) means the safety_watcher
service evaluates risk with byte-identical logic to the gateway.
"""

from __future__ import annotations

import logging
from typing import Any, Iterable, Optional, Sequence

import asyncpg

from app.config import settings

log = logging.getLogger(__name__)


# ============================================================
# Time-of-day
# ============================================================
async def is_night(conn: asyncpg.Connection) -> bool:
    """Night per the database's Asia/Kolkata clock.

    Resolved server-side so the gateway, watcher and any cron job all agree
    on when "night risk" applies, regardless of container timezone.
    """
    return bool(await conn.fetchval("SELECT fn_is_night()"))


# ============================================================
# Telemetry ingest
# ============================================================
async def insert_telemetry(
    conn: asyncpg.Connection,
    trip_id: str,
    user_id: str,
    lat: float,
    lon: float,
    accuracy_m: Optional[float] = None,
    speed_kmh: Optional[float] = None,
    heading_deg: Optional[float] = None,
    altitude_m: Optional[float] = None,
    battery_pct: Optional[int] = None,
) -> None:
    """Append one GPS fix. Note ST_MakePoint takes (lon, lat), in that order."""
    await conn.execute(
        """
        INSERT INTO live_gps_telemetry
            (trip_id, user_id, location, accuracy_m, speed_kmh,
             heading_deg, altitude_m, battery_pct)
        VALUES
            ($1, $2, ST_SetSRID(ST_MakePoint($3, $4), 4326), $5, $6, $7, $8, $9)
        """,
        trip_id, user_id, lon, lat,
        accuracy_m, speed_kmh, heading_deg, altitude_m, battery_pct,
    )


async def last_position(conn: asyncpg.Connection, trip_id: str) -> Optional[dict[str, Any]]:
    """Most recent fix for a trip, or None if it has never reported."""
    row = await conn.fetchrow("SELECT * FROM fn_trip_last_position($1)", trip_id)
    return dict(row) if row else None


# ============================================================
# Core safety evaluation
# ============================================================
async def check_risk_and_deviation(
    conn: asyncpg.Connection,
    trip_id: str,
    lat: float,
    lon: float,
    night_mode: Optional[bool] = None,
) -> dict[str, Any]:
    """Full safety picture for one coordinate on one trip.

    Returns:
        in_high_risk:   inside a zone at/above RISK_THRESHOLD
        max_risk:       worst effective risk score found (1-5, 0 if none)
        risk_zones:     the matching zones, worst first
        off_route:      deviating beyond OFF_ROUTE_THRESHOLD_M
        nearby_alerts:  live crowdsourced reports in range
        safe_refuges:   nearest low-risk zones (only queried when at risk)
        safety_score:   blended 0-5 score for this point
        night_mode:     which risk profile was applied
    """
    if night_mode is None:
        night_mode = await is_night(conn)

    risk_rows = await conn.fetch(
        "SELECT * FROM fn_get_risk_zone($1, $2, $3, $4)",
        lat, lon, settings.RISK_THRESHOLD, night_mode,
    )
    risk_zones = [dict(r) for r in risk_rows]
    in_high_risk = len(risk_zones) > 0
    # fn_get_risk_zone orders worst-first, so row 0 is the ceiling
    max_risk = int(risk_zones[0]["risk_score"]) if risk_zones else 0

    off_route = await conn.fetchval(
        "SELECT fn_is_off_route($1, $2, $3, $4)",
        trip_id, lat, lon, settings.OFF_ROUTE_THRESHOLD_M,
    )

    alert_rows = await conn.fetch(
        "SELECT * FROM fn_nearby_alerts($1, $2, $3)",
        lat, lon, settings.NEARBY_ALERT_RADIUS_M,
    )

    safety_score = await conn.fetchval(
        "SELECT fn_point_safety_score($1, $2, $3)", lat, lon, night_mode
    )

    # Only look for refuges when they are actually needed — this is the
    # most expensive query of the set.
    refuges: list[dict[str, Any]] = []
    if in_high_risk:
        refuge_rows = await conn.fetch(
            "SELECT * FROM fn_find_safe_refuges($1, $2, $3, $4)",
            lat, lon, settings.REFUGE_SEARCH_RADIUS_M, settings.REFUGE_MAX_RISK,
        )
        refuges = [dict(r) for r in refuge_rows]

    return {
        "in_high_risk": in_high_risk,
        "max_risk": max_risk,
        "risk_zones": risk_zones,
        "off_route": bool(off_route),
        "nearby_alerts": [dict(a) for a in alert_rows],
        "safe_refuges": refuges,
        "safety_score": float(safety_score) if safety_score is not None else None,
        "night_mode": night_mode,
    }


# ============================================================
# Scoring helpers (consumed by the AI engine)
# ============================================================
async def point_safety_score(
    conn: asyncpg.Connection,
    lat: float,
    lon: float,
    night_mode: Optional[bool] = None,
) -> float:
    """0-5 safety score for a single coordinate (5 = safest)."""
    value = await conn.fetchval(
        "SELECT fn_point_safety_score($1, $2, $3)", lat, lon, night_mode
    )
    return float(value) if value is not None else 3.5


async def score_points(
    conn: asyncpg.Connection,
    points: Sequence[tuple[float, float]],
    night_mode: Optional[bool] = None,
) -> list[float]:
    """Batch-score coordinates in one round trip.

    The AI engine scores every leg midpoint of every candidate route, which
    is easily 30+ points per plan. Issuing them as a single unnested query
    keeps that to one network hop instead of 30.
    """
    if not points:
        return []

    lats = [float(p[0]) for p in points]
    lons = [float(p[1]) for p in points]

    rows = await conn.fetch(
        """
        SELECT fn_point_safety_score(t.lat, t.lon, $3) AS score
        FROM UNNEST($1::DOUBLE PRECISION[], $2::DOUBLE PRECISION[])
             WITH ORDINALITY AS t(lat, lon, ord)
        ORDER BY t.ord
        """,
        lats, lons, night_mode,
    )
    return [float(r["score"]) for r in rows]


async def route_safety_score(
    conn: asyncpg.Connection,
    coordinates: Iterable[tuple[float, float]],
    night_mode: Optional[bool] = None,
) -> float:
    """Safety score for a polyline given as (lat, lon) pairs.

    Falls back to point scoring when fewer than two coordinates are supplied,
    since a LineString needs at least two.
    """
    coords = [(float(a), float(b)) for a, b in coordinates]
    if not coords:
        return 3.5
    if len(coords) == 1:
        return await point_safety_score(conn, coords[0][0], coords[0][1], night_mode)

    # WKT expects "lon lat" ordering
    wkt = "LINESTRING(" + ", ".join(f"{lon} {lat}" for lat, lon in coords) + ")"
    value = await conn.fetchval(
        "SELECT fn_route_safety_score(ST_GeomFromText($1, 4326), $2)", wkt, night_mode
    )
    return float(value) if value is not None else 3.5


# ============================================================
# Reads for the map UI
# ============================================================
async def nearby_alerts(
    conn: asyncpg.Connection,
    lat: float,
    lon: float,
    radius_m: float = 300.0,
) -> list[dict[str, Any]]:
    rows = await conn.fetch("SELECT * FROM fn_nearby_alerts($1, $2, $3)", lat, lon, radius_m)
    return [dict(r) for r in rows]


async def safe_refuges(
    conn: asyncpg.Connection,
    lat: float,
    lon: float,
    radius_m: float = 600.0,
    max_risk: int = 2,
) -> list[dict[str, Any]]:
    rows = await conn.fetch(
        "SELECT * FROM fn_find_safe_refuges($1, $2, $3, $4)", lat, lon, radius_m, max_risk
    )
    return [dict(r) for r in rows]


async def zones_in_bbox(
    conn: asyncpg.Connection,
    min_lat: float,
    min_lon: float,
    max_lat: float,
    max_lon: float,
    city: Optional[str] = None,
) -> list[dict[str, Any]]:
    """Active zones intersecting a viewport, as GeoJSON for the map overlay.

    ST_MakeEnvelope argument order is (xmin, ymin, xmax, ymax) = lon/lat.
    """
    rows = await conn.fetch(
        """
        SELECT
            sz.id,
            sz.name,
            sz.city,
            sz.risk_score,
            sz.night_risk_score,
            sz.time_sensitive,
            sz.risk_factors,
            sz.verified_by,
            ST_AsGeoJSON(sz.zone_polygon) AS geojson,
            ST_Y(ST_Centroid(sz.zone_polygon))::DOUBLE PRECISION AS center_lat,
            ST_X(ST_Centroid(sz.zone_polygon))::DOUBLE PRECISION AS center_lon
        FROM safety_zones sz
        WHERE sz.active = TRUE
          AND ($5::TEXT IS NULL OR sz.city ILIKE $5)
          AND ST_Intersects(
                  sz.zone_polygon,
                  ST_MakeEnvelope($2, $1, $4, $3, 4326)
              )
        ORDER BY sz.risk_score DESC
        LIMIT 200
        """,
        min_lat, min_lon, max_lat, max_lon, city,
    )
    return [dict(r) for r in rows]


async def insert_report(
    conn: asyncpg.Connection,
    user_id: Optional[str],
    lat: float,
    lon: float,
    category: str,
    description: Optional[str],
    severity: int,
    ttl_hours: int = 24,
) -> dict[str, Any]:
    """Record a crowdsourced report and return the stored row."""
    row = await conn.fetchrow(
        """
        INSERT INTO crowdsourced_reports
            (user_id, report_point, category, description, severity, expires_at)
        VALUES
            ($1, ST_SetSRID(ST_MakePoint($2, $3), 4326), $4, $5, $6,
             NOW() + ($7 || ' hours')::INTERVAL)
        RETURNING id, category, severity, description, verified,
                  upvotes, downvotes, expires_at, created_at,
                  ST_Y(report_point)::DOUBLE PRECISION AS lat,
                  ST_X(report_point)::DOUBLE PRECISION AS lon
        """,
        user_id, lon, lat, category, description, severity, str(ttl_hours),
    )
    return dict(row)


async def vote_report(
    conn: asyncpg.Connection,
    report_id: str,
    direction: str,
) -> Optional[dict[str, Any]]:
    """Apply an up/down vote. Returns None when the report does not exist."""
    column = "upvotes" if direction == "up" else "downvotes"
    row = await conn.fetchrow(
        f"""
        UPDATE crowdsourced_reports
        SET {column} = {column} + 1
        WHERE id = $1
        RETURNING id, upvotes, downvotes
        """,  # noqa: S608 — `column` is chosen from a fixed pair above
        report_id,
    )
    return dict(row) if row else None
