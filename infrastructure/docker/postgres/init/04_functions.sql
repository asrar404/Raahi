-- ============================================================
-- RAAHI — 04: Spatial & Safety Functions
--
-- Two conventions used throughout, both verified against PostgreSQL 18:
--
-- 1. `#variable_conflict use_column`. In a RETURNS TABLE function the output
--    parameter names are also PL/pgSQL variables, so an *unqualified* column
--    reference that shares a name with one of them fails at runtime with
--    "column reference is ambiguous". Qualified references (sz.risk_score) are
--    fine, and so are ORDER BY clauses naming a select-list alias — so this
--    pragma is defensive hardening rather than a fix for a present bug. It
--    means a later edit that drops a table alias cannot introduce one.
--
-- 2. Explicit ::TEXT casts on VARCHAR columns. This one is not optional.
--    RETURN QUERY matches the declared result type strictly, and returning a
--    VARCHAR(30) column where TEXT was declared aborts with "structure of
--    query does not match function result type". fn_nearby_alerts returns
--    crowdsourced_reports.category, which is VARCHAR(30), hence the cast.
--
-- Distances are always computed on the GEOGRAPHY cast, so every
-- *_m value below is true metres on the spheroid, not degrees.
-- ============================================================

-- ============================================================
-- HELPER: is it night in India right now?
-- Night runs 22:00 -> 06:00 IST. Times are evaluated in Asia/Kolkata
-- rather than by bolting +5 onto UTC, so DST-free IST stays correct.
-- ============================================================
CREATE OR REPLACE FUNCTION fn_is_night(p_at TIMESTAMPTZ DEFAULT NOW())
RETURNS BOOLEAN AS $$
DECLARE
    v_hour INT;
BEGIN
    v_hour := EXTRACT(HOUR FROM (p_at AT TIME ZONE 'Asia/Kolkata'));
    RETURN v_hour < 6 OR v_hour >= 22;
END;
$$ LANGUAGE plpgsql STABLE;

COMMENT ON FUNCTION fn_is_night IS 'TRUE between 22:00 and 06:00 IST';

-- ============================================================
-- 1. fn_get_risk_zone
-- Every active zone containing the point whose effective risk meets
-- p_min_risk. Effective risk swaps to night_risk_score for
-- time-sensitive zones when p_night_mode is TRUE.
-- Highest risk first, so callers can read row 0 as "worst case".
-- ============================================================
CREATE OR REPLACE FUNCTION fn_get_risk_zone(
    p_lat        DOUBLE PRECISION,
    p_lon        DOUBLE PRECISION,
    p_min_risk   SMALLINT DEFAULT 3,
    p_night_mode BOOLEAN  DEFAULT FALSE
)
RETURNS TABLE (
    zone_id      UUID,
    zone_name    TEXT,
    risk_score   SMALLINT,
    risk_factors TEXT[]
) AS $$
#variable_conflict use_column
DECLARE
    v_point GEOMETRY;
BEGIN
    -- ST_MakePoint takes (x, y) = (longitude, latitude)
    v_point := ST_SetSRID(ST_MakePoint(p_lon, p_lat), 4326);

    RETURN QUERY
    SELECT q.zid, q.zname, q.zrisk, q.zfactors
    FROM (
        SELECT
            sz.id   AS zid,
            sz.name AS zname,
            CASE
                WHEN p_night_mode AND sz.time_sensitive
                    THEN COALESCE(sz.night_risk_score, sz.risk_score)
                ELSE sz.risk_score
            END AS zrisk,
            sz.risk_factors AS zfactors
        FROM safety_zones sz
        WHERE sz.active = TRUE
          AND ST_Contains(sz.zone_polygon, v_point)
    ) q
    WHERE q.zrisk >= p_min_risk
    ORDER BY q.zrisk DESC;
END;
$$ LANGUAGE plpgsql STABLE;

COMMENT ON FUNCTION fn_get_risk_zone
    IS 'Active zones containing (lat,lon) at or above p_min_risk, worst first';

-- ============================================================
-- 2. fn_find_safe_refuges
-- Low-risk zones near a point, nearest first. Called when SOS fires so
-- the app can say "walk 200 m to Saket District Centre".
-- ============================================================
CREATE OR REPLACE FUNCTION fn_find_safe_refuges(
    p_lat      DOUBLE PRECISION,
    p_lon      DOUBLE PRECISION,
    p_radius_m DOUBLE PRECISION DEFAULT 500.0,
    p_max_risk SMALLINT         DEFAULT 2
)
RETURNS TABLE (
    zone_id    UUID,
    zone_name  TEXT,
    risk_score SMALLINT,
    distance_m DOUBLE PRECISION
) AS $$
#variable_conflict use_column
DECLARE
    v_point GEOGRAPHY;
BEGIN
    v_point := ST_SetSRID(ST_MakePoint(p_lon, p_lat), 4326)::GEOGRAPHY;

    RETURN QUERY
    SELECT q.zid, q.zname, q.zrisk, q.dist
    FROM (
        SELECT
            sz.id         AS zid,
            sz.name       AS zname,
            sz.risk_score AS zrisk,
            ST_Distance(ST_Centroid(sz.zone_polygon)::GEOGRAPHY, v_point) AS dist
        FROM safety_zones sz
        WHERE sz.active = TRUE
          AND sz.risk_score <= p_max_risk
          AND ST_DWithin(ST_Centroid(sz.zone_polygon)::GEOGRAPHY, v_point, p_radius_m)
    ) q
    ORDER BY q.dist ASC
    LIMIT 10;
END;
$$ LANGUAGE plpgsql STABLE;

COMMENT ON FUNCTION fn_find_safe_refuges
    IS 'Nearest low-risk zones within p_radius_m metres';

-- ============================================================
-- 3. fn_is_off_route
-- TRUE when the user is further than p_threshold_m from every
-- in-progress leg polyline of the trip.
--
-- Returns FALSE (not TRUE) when no in-progress leg has a route_line:
-- absence of a reference path is not evidence of deviation, and a
-- false SOS is far more damaging than a missed one here.
-- ============================================================
CREATE OR REPLACE FUNCTION fn_is_off_route(
    p_trip_id     UUID,
    p_lat         DOUBLE PRECISION,
    p_lon         DOUBLE PRECISION,
    p_threshold_m DOUBLE PRECISION DEFAULT 300.0
)
RETURNS BOOLEAN AS $$
DECLARE
    v_point    GEOGRAPHY;
    v_min_dist DOUBLE PRECISION;
BEGIN
    v_point := ST_SetSRID(ST_MakePoint(p_lon, p_lat), 4326)::GEOGRAPHY;

    SELECT MIN(ST_Distance(tl.route_line::GEOGRAPHY, v_point))
    INTO v_min_dist
    FROM trip_legs tl
    WHERE tl.trip_id = p_trip_id
      AND tl.status = 'in_progress'
      AND tl.route_line IS NOT NULL;

    IF v_min_dist IS NULL THEN
        RETURN FALSE;
    END IF;

    RETURN v_min_dist > p_threshold_m;
END;
$$ LANGUAGE plpgsql STABLE;

COMMENT ON FUNCTION fn_is_off_route
    IS 'TRUE when the point deviates more than p_threshold_m from all in-progress legs';

-- ============================================================
-- 4. fn_nearby_alerts
-- Live crowdsourced reports near a point. Drops expired reports and
-- anything the community has downvoted 5+ times.
-- ============================================================
CREATE OR REPLACE FUNCTION fn_nearby_alerts(
    p_lat      DOUBLE PRECISION,
    p_lon      DOUBLE PRECISION,
    p_radius_m DOUBLE PRECISION DEFAULT 300.0
)
RETURNS TABLE (
    report_id   UUID,
    category    TEXT,
    severity    SMALLINT,
    distance_m  DOUBLE PRECISION,
    description TEXT,
    lat         DOUBLE PRECISION,
    lon         DOUBLE PRECISION,
    created_at  TIMESTAMPTZ
) AS $$
#variable_conflict use_column
DECLARE
    v_point GEOGRAPHY;
BEGIN
    v_point := ST_SetSRID(ST_MakePoint(p_lon, p_lat), 4326)::GEOGRAPHY;

    RETURN QUERY
    SELECT q.rid, q.rcat, q.rsev, q.dist, q.rdesc, q.rlat, q.rlon, q.rcreated
    FROM (
        SELECT
            cr.id                AS rid,
            cr.category::TEXT    AS rcat,   -- explicit cast: column is VARCHAR(30)
            cr.severity          AS rsev,
            ST_Distance(cr.report_point::GEOGRAPHY, v_point) AS dist,
            cr.description       AS rdesc,
            ST_Y(cr.report_point)::DOUBLE PRECISION AS rlat,
            ST_X(cr.report_point)::DOUBLE PRECISION AS rlon,
            cr.created_at        AS rcreated
        FROM crowdsourced_reports cr
        WHERE cr.expires_at > NOW()
          AND cr.downvotes < 5
          AND ST_DWithin(cr.report_point::GEOGRAPHY, v_point, p_radius_m)
    ) q
    ORDER BY q.rsev DESC, q.dist ASC
    LIMIT 20;
END;
$$ LANGUAGE plpgsql STABLE;

COMMENT ON FUNCTION fn_nearby_alerts
    IS 'Unexpired, non-suppressed reports within p_radius_m, most severe first';

-- ============================================================
-- 5. fn_point_safety_score
-- Collapses zone risk plus live reports into a single 0-5 safety score
-- (5 = safest). This is what the AI engine samples per leg midpoint.
--
--   zone risk 1..5  ->  base safety 5..1
--   no zone data    ->  3.5 (mildly optimistic neutral)
--   negative report ->  -0.3 per severity point, distance-attenuated
--   safe_spot /
--   police_present  ->  +0.25 each
-- ============================================================
CREATE OR REPLACE FUNCTION fn_point_safety_score(
    p_lat        DOUBLE PRECISION,
    p_lon        DOUBLE PRECISION,
    p_night_mode BOOLEAN DEFAULT NULL,
    p_radius_m   DOUBLE PRECISION DEFAULT 300.0
)
RETURNS NUMERIC AS $$
DECLARE
    v_point      GEOGRAPHY;
    v_night      BOOLEAN;
    v_worst_risk SMALLINT;
    v_score      NUMERIC;
    v_penalty    NUMERIC := 0;
    v_bonus      NUMERIC := 0;
BEGIN
    v_night := COALESCE(p_night_mode, fn_is_night());
    v_point := ST_SetSRID(ST_MakePoint(p_lon, p_lat), 4326)::GEOGRAPHY;

    -- Worst effective risk among containing zones
    SELECT MAX(
        CASE
            WHEN v_night AND sz.time_sensitive
                THEN COALESCE(sz.night_risk_score, sz.risk_score)
            ELSE sz.risk_score
        END
    )
    INTO v_worst_risk
    FROM safety_zones sz
    WHERE sz.active = TRUE
      AND ST_Contains(sz.zone_polygon, ST_SetSRID(ST_MakePoint(p_lon, p_lat), 4326));

    IF v_worst_risk IS NULL THEN
        v_score := 3.5;
    ELSE
        v_score := 6 - v_worst_risk;
    END IF;

    -- Negative reports drag the score down, weighted by proximity
    SELECT COALESCE(SUM(
        0.3 * cr.severity
            * (1 - LEAST(ST_Distance(cr.report_point::GEOGRAPHY, v_point) / p_radius_m, 1))
    ), 0)
    INTO v_penalty
    FROM crowdsourced_reports cr
    WHERE cr.expires_at > NOW()
      AND cr.downvotes < 5
      AND cr.category NOT IN ('safe_spot', 'police_present')
      AND ST_DWithin(cr.report_point::GEOGRAPHY, v_point, p_radius_m);

    -- Positive signals nudge it back up
    SELECT COALESCE(COUNT(*) * 0.25, 0)
    INTO v_bonus
    FROM crowdsourced_reports cr
    WHERE cr.expires_at > NOW()
      AND cr.category IN ('safe_spot', 'police_present')
      AND ST_DWithin(cr.report_point::GEOGRAPHY, v_point, p_radius_m);

    -- Night is inherently riskier even outside a flagged zone
    IF v_night THEN
        v_score := v_score - 0.3;
    END IF;

    RETURN ROUND(GREATEST(0, LEAST(5, v_score - v_penalty + v_bonus)), 2);
END;
$$ LANGUAGE plpgsql STABLE;

COMMENT ON FUNCTION fn_point_safety_score
    IS 'Blended 0-5 safety score for a coordinate (5 = safest)';

-- ============================================================
-- 6. fn_route_safety_score
-- Samples a LineString at p_samples evenly spaced fractions and averages
-- fn_point_safety_score. The minimum is blended in at 30% so one
-- genuinely dangerous stretch cannot hide behind a safe average.
-- ============================================================
CREATE OR REPLACE FUNCTION fn_route_safety_score(
    p_line       GEOMETRY,
    p_night_mode BOOLEAN DEFAULT NULL,
    p_samples    INT     DEFAULT 8
)
RETURNS NUMERIC AS $$
DECLARE
    v_avg   NUMERIC;
    v_min   NUMERIC;
BEGIN
    IF p_line IS NULL OR ST_IsEmpty(p_line) THEN
        RETURN 3.5;
    END IF;

    WITH samples AS (
        SELECT ST_LineInterpolatePoint(
                   ST_LineMerge(p_line),
                   i::DOUBLE PRECISION / GREATEST(p_samples - 1, 1)
               ) AS pt
        FROM generate_series(0, GREATEST(p_samples - 1, 1)) AS i
    ),
    scores AS (
        SELECT fn_point_safety_score(ST_Y(pt), ST_X(pt), p_night_mode) AS s
        FROM samples
    )
    SELECT AVG(s), MIN(s) INTO v_avg, v_min FROM scores;

    RETURN ROUND(COALESCE(v_avg * 0.7 + v_min * 0.3, 3.5), 2);
END;
$$ LANGUAGE plpgsql STABLE;

COMMENT ON FUNCTION fn_route_safety_score
    IS 'Safety score for a polyline: 70% mean of samples, 30% worst sample';

-- ============================================================
-- 7. fn_trip_budget_status
-- Single round trip for the budget widget and over-budget alerts.
-- ============================================================
CREATE OR REPLACE FUNCTION fn_trip_budget_status(p_trip_id UUID)
RETURNS TABLE (
    ceiling      NUMERIC,
    planned      NUMERIC,
    spent        NUMERIC,
    remaining    NUMERIC,
    percent_used NUMERIC,
    over_budget  BOOLEAN
) AS $$
#variable_conflict use_column
BEGIN
    RETURN QUERY
    SELECT
        q.c,
        q.p,
        q.s,
        ROUND(q.c - q.s, 2),
        CASE WHEN q.c > 0 THEN ROUND(q.s / q.c * 100, 2) ELSE 0::NUMERIC END,
        q.s > q.c
    FROM (
        SELECT
            t.budget_ceiling                    AS c,
            COALESCE(t.total_planned_cost, 0)   AS p,
            COALESCE((
                SELECT SUM(el.amount)
                FROM expense_logs el
                WHERE el.trip_id = t.id
            ), 0)                               AS s
        FROM trips t
        WHERE t.id = p_trip_id
    ) q;
END;
$$ LANGUAGE plpgsql STABLE;

-- ============================================================
-- 8. fn_ensure_telemetry_partition
-- Idempotently creates the monthly partition covering p_target.
-- Call from a scheduled job (or on service startup) so telemetry never
-- has to fall back to live_gps_telemetry_default.
-- ============================================================
CREATE OR REPLACE FUNCTION fn_ensure_telemetry_partition(
    p_target DATE DEFAULT CURRENT_DATE
)
RETURNS TEXT AS $$
DECLARE
    v_start DATE;
    v_end   DATE;
    v_name  TEXT;
BEGIN
    v_start := DATE_TRUNC('month', p_target)::DATE;
    v_end   := (v_start + INTERVAL '1 month')::DATE;
    v_name  := FORMAT('live_gps_telemetry_%s', TO_CHAR(v_start, 'YYYY_MM'));

    IF EXISTS (SELECT 1 FROM pg_class WHERE relname = v_name) THEN
        RETURN FORMAT('%s already exists', v_name);
    END IF;

    EXECUTE FORMAT(
        'CREATE TABLE %I PARTITION OF live_gps_telemetry FOR VALUES FROM (%L) TO (%L)',
        v_name, v_start, v_end
    );
    RETURN FORMAT('created %s', v_name);
END;
$$ LANGUAGE plpgsql VOLATILE;

-- ============================================================
-- 9. fn_purge_expired_reports
-- Housekeeping: drop unverified reports that expired over a day ago.
-- ============================================================
CREATE OR REPLACE FUNCTION fn_purge_expired_reports()
RETURNS INT AS $$
DECLARE
    v_deleted INT;
BEGIN
    DELETE FROM crowdsourced_reports
    WHERE verified = FALSE
      AND expires_at < NOW() - INTERVAL '1 day';
    GET DIAGNOSTICS v_deleted = ROW_COUNT;
    RETURN v_deleted;
END;
$$ LANGUAGE plpgsql VOLATILE;

-- ============================================================
-- 10. fn_trip_last_position
-- Latest telemetry fix for a trip; used by SOS to attach a location
-- when the triggering payload has none.
-- ============================================================
CREATE OR REPLACE FUNCTION fn_trip_last_position(p_trip_id UUID)
RETURNS TABLE (
    lat         DOUBLE PRECISION,
    lon         DOUBLE PRECISION,
    recorded_at TIMESTAMPTZ
) AS $$
#variable_conflict use_column
BEGIN
    RETURN QUERY
    SELECT
        ST_Y(t.location)::DOUBLE PRECISION,
        ST_X(t.location)::DOUBLE PRECISION,
        t.recorded_at
    FROM live_gps_telemetry t
    WHERE t.trip_id = p_trip_id
    ORDER BY t.recorded_at DESC
    LIMIT 1;
END;
$$ LANGUAGE plpgsql STABLE;
