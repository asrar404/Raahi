-- ============================================================
-- RAAHI — 03: Indexes
--
-- GiST for every geometry column (spatial predicates), B-tree for
-- foreign keys, status filters and time-ordered lookups.
-- ============================================================

-- ============================================================
-- SPATIAL INDEXES (GiST)
-- ============================================================
CREATE INDEX idx_trips_origin_gist     ON trips        USING GIST (origin_point);
CREATE INDEX idx_trips_dest_gist       ON trips        USING GIST (dest_point);
CREATE INDEX idx_trip_legs_from_gist   ON trip_legs    USING GIST (from_point);
CREATE INDEX idx_trip_legs_to_gist     ON trip_legs    USING GIST (to_point);
CREATE INDEX idx_trip_legs_route_gist  ON trip_legs    USING GIST (route_line);
CREATE INDEX idx_safety_zones_polygon  ON safety_zones USING GIST (zone_polygon);
CREATE INDEX idx_crowdsourced_point    ON crowdsourced_reports USING GIST (report_point);
CREATE INDEX idx_telemetry_location    ON live_gps_telemetry   USING GIST (location);
CREATE INDEX idx_stayfood_location     ON stay_and_food_recommendations USING GIST (location);
CREATE INDEX idx_sos_events_location   ON sos_events   USING GIST (location);

-- fn_find_safe_refuges measures from the polygon centroid and casts to
-- GEOGRAPHY, so index the exact expression it searches on.
CREATE INDEX idx_safety_zones_centroid_geog
    ON safety_zones USING GIST ((ST_Centroid(zone_polygon)::GEOGRAPHY))
    WHERE active = TRUE;

-- fn_nearby_alerts / fn_is_off_route both use GEOGRAPHY casts
CREATE INDEX idx_crowdsourced_point_geog
    ON crowdsourced_reports USING GIST ((report_point::GEOGRAPHY));
CREATE INDEX idx_trip_legs_route_geog
    ON trip_legs USING GIST ((route_line::GEOGRAPHY))
    WHERE route_line IS NOT NULL;

-- ============================================================
-- FOREIGN KEY / LOOKUP INDEXES (B-tree)
-- ============================================================
CREATE INDEX idx_users_supabase_uid  ON users (supabase_uid);
CREATE INDEX idx_trips_user_id       ON trips (user_id);
CREATE INDEX idx_trips_status        ON trips (status);
CREATE INDEX idx_trips_created_at    ON trips (created_at DESC);
CREATE INDEX idx_trip_legs_trip_id   ON trip_legs (trip_id, leg_order);
CREATE INDEX idx_expense_trip_id     ON expense_logs (trip_id, recorded_at DESC);
CREATE INDEX idx_expense_leg_id      ON expense_logs (leg_id);
CREATE INDEX idx_stayfood_trip_id    ON stay_and_food_recommendations (trip_id);
CREATE INDEX idx_sos_events_trip     ON sos_events (trip_id, created_at DESC);
CREATE INDEX idx_sos_events_user     ON sos_events (user_id, created_at DESC);

-- ============================================================
-- PARTIAL / TARGETED INDEXES
-- ============================================================

-- The watcher only ever polls trips that are live
CREATE INDEX idx_trips_active
    ON trips (user_id, status)
    WHERE status IN ('active', 'sos');

-- The reroute check scans in-progress legs for one trip
CREATE INDEX idx_trip_legs_in_progress
    ON trip_legs (trip_id)
    WHERE status = 'in_progress';

-- Report expiry sweeper
CREATE INDEX idx_reports_expires
    ON crowdsourced_reports (expires_at)
    WHERE verified = FALSE;

-- fn_nearby_alerts filters on expiry then sorts by severity
CREATE INDEX idx_reports_live
    ON crowdsourced_reports (severity DESC, expires_at)
    WHERE downvotes < 5;

CREATE INDEX idx_safety_zones_city  ON safety_zones (city, active);
CREATE INDEX idx_safety_zones_risk  ON safety_zones (risk_score) WHERE active = TRUE;

-- Unresolved SOS events (should always be a tiny set)
CREATE INDEX idx_sos_events_open
    ON sos_events (created_at DESC)
    WHERE resolved = FALSE;

-- Women-friendly + budget filtering on recommendations
CREATE INDEX idx_stayfood_filters
    ON stay_and_food_recommendations (category, price_per_unit)
    WHERE women_friendly = TRUE;

-- ============================================================
-- TELEMETRY INDEXES
-- Declared on the parent; PostgreSQL cascades them to every existing
-- and future partition automatically.
-- ============================================================
CREATE INDEX idx_telemetry_trip_time ON live_gps_telemetry (trip_id, recorded_at DESC);
CREATE INDEX idx_telemetry_user_time ON live_gps_telemetry (user_id, recorded_at DESC);

-- ============================================================
-- FUZZY TEXT SEARCH (pg_trgm)
-- Powers "did you mean Paharganj?" place lookups
-- ============================================================
CREATE INDEX idx_safety_zones_name_trgm
    ON safety_zones USING GIN (name gin_trgm_ops);
CREATE INDEX idx_stayfood_name_trgm
    ON stay_and_food_recommendations USING GIN (name gin_trgm_ops);
CREATE INDEX idx_trips_place_names_trgm
    ON trips USING GIN ((origin_name || ' ' || dest_name) gin_trgm_ops);
