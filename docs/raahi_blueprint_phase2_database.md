# RAAHI — Phase 2: Database Scripts (PostgreSQL + PostGIS)

## File: `infrastructure/docker/postgres/init/01_extensions.sql`

```sql
-- Enable required extensions
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS postgis_topology;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS pg_trgm;  -- for fuzzy text search on place names
```

---

## File: `infrastructure/docker/postgres/init/02_schema.sql`

```sql
-- ============================================================
-- USERS
-- ============================================================
CREATE TABLE users (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    supabase_uid    TEXT UNIQUE NOT NULL,
    full_name       TEXT NOT NULL,
    phone           VARCHAR(15) UNIQUE NOT NULL,
    email           TEXT UNIQUE,
    gender          VARCHAR(10) CHECK (gender IN ('female','male','other')),
    preferred_modes TEXT[] DEFAULT ARRAY['metro','bus','auto'],
    budget_default  NUMERIC(10,2) DEFAULT 500.00,
    emergency_contacts JSONB DEFAULT '[]',  -- [{name, phone, relation}]
    sos_enabled     BOOLEAN DEFAULT TRUE,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

-- ============================================================
-- TRIPS
-- ============================================================
CREATE TABLE trips (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    status          VARCHAR(20) DEFAULT 'planned'
                        CHECK (status IN ('planned','active','completed','cancelled','sos')),
    origin_name     TEXT NOT NULL,
    origin_point    GEOMETRY(Point, 4326) NOT NULL,
    dest_name       TEXT NOT NULL,
    dest_point      GEOMETRY(Point, 4326) NOT NULL,
    budget_ceiling  NUMERIC(10,2) NOT NULL,
    time_deadline   TIMESTAMPTZ,
    transit_prefs   TEXT[] DEFAULT ARRAY['metro','bus','auto','cab'],
    total_planned_cost NUMERIC(10,2),
    total_actual_cost  NUMERIC(10,2) DEFAULT 0,
    planned_eta     TIMESTAMPTZ,
    actual_eta      TIMESTAMPTZ,
    utility_score   NUMERIC(5,2),
    started_at      TIMESTAMPTZ,
    completed_at    TIMESTAMPTZ,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- ============================================================
-- TRIP LEGS (individual transit segments)
-- ============================================================
CREATE TABLE trip_legs (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    trip_id         UUID NOT NULL REFERENCES trips(id) ON DELETE CASCADE,
    leg_order       SMALLINT NOT NULL,
    mode            VARCHAR(20) NOT NULL
                        CHECK (mode IN ('walk','metro','bus','train','auto','cab','rapido','ferry')),
    from_name       TEXT NOT NULL,
    from_point      GEOMETRY(Point, 4326) NOT NULL,
    to_name         TEXT NOT NULL,
    to_point        GEOMETRY(Point, 4326) NOT NULL,
    route_line      GEOMETRY(LineString, 4326),
    distance_km     NUMERIC(8,3),
    planned_cost    NUMERIC(8,2) DEFAULT 0,
    actual_cost     NUMERIC(8,2),
    planned_duration_mins INT,
    actual_duration_mins  INT,
    provider        TEXT,                    -- e.g. 'Delhi Metro', 'BEST', 'Ola'
    booking_ref     TEXT,
    status          VARCHAR(20) DEFAULT 'pending'
                        CHECK (status IN ('pending','in_progress','completed','skipped')),
    departed_at     TIMESTAMPTZ,
    arrived_at      TIMESTAMPTZ,
    safety_score    NUMERIC(3,2) CHECK (safety_score BETWEEN 0 AND 5),
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- ============================================================
-- STAY & FOOD RECOMMENDATIONS
-- ============================================================
CREATE TABLE stay_and_food_recommendations (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    trip_id         UUID REFERENCES trips(id) ON DELETE SET NULL,
    category        VARCHAR(10) NOT NULL CHECK (category IN ('stay','food')),
    name            TEXT NOT NULL,
    location        GEOMETRY(Point, 4326) NOT NULL,
    address         TEXT,
    price_per_unit  NUMERIC(8,2),           -- per night (stay) or per meal (food)
    rating          NUMERIC(2,1) CHECK (rating BETWEEN 0 AND 5),
    safety_verified BOOLEAN DEFAULT FALSE,
    women_friendly  BOOLEAN DEFAULT FALSE,
    tags            TEXT[],                  -- ['budget','24hr','wifi','veg']
    source          VARCHAR(20),             -- 'google','zomato','booking','manual'
    external_id     TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- ============================================================
-- SAFETY ZONES (Polygon geofences with risk scores)
-- ============================================================
CREATE TABLE safety_zones (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name            TEXT NOT NULL,
    city            TEXT NOT NULL,
    zone_polygon    GEOMETRY(Polygon, 4326) NOT NULL,
    risk_score      SMALLINT NOT NULL CHECK (risk_score BETWEEN 1 AND 5),
                    -- 1=very safe, 5=very high risk
    risk_factors    TEXT[],                  -- ['poor_lighting','crime_reports','isolated']
    time_sensitive  BOOLEAN DEFAULT FALSE,   -- risk changes by time of day
    night_risk_score SMALLINT CHECK (night_risk_score BETWEEN 1 AND 5),
    verified_by     VARCHAR(20) DEFAULT 'ml' CHECK (verified_by IN ('ml','admin','community')),
    active          BOOLEAN DEFAULT TRUE,
    last_updated    TIMESTAMPTZ DEFAULT NOW(),
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- ============================================================
-- CROWDSOURCED REPORTS
-- ============================================================
CREATE TABLE crowdsourced_reports (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id         UUID REFERENCES users(id) ON DELETE SET NULL,
    report_point    GEOMETRY(Point, 4326) NOT NULL,
    category        VARCHAR(30) NOT NULL
                        CHECK (category IN (
                            'harassment','theft','poor_lighting',
                            'unsafe_area','accident','flooding',
                            'road_blocked','safe_spot','police_present'
                        )),
    description     TEXT,
    severity        SMALLINT DEFAULT 3 CHECK (severity BETWEEN 1 AND 5),
    verified        BOOLEAN DEFAULT FALSE,
    upvotes         INT DEFAULT 0,
    downvotes       INT DEFAULT 0,
    expires_at      TIMESTAMPTZ DEFAULT NOW() + INTERVAL '24 hours',
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- ============================================================
-- LIVE GPS TELEMETRY (append-only hot table)
-- ============================================================
CREATE TABLE live_gps_telemetry (
    id              BIGSERIAL PRIMARY KEY,
    trip_id         UUID NOT NULL REFERENCES trips(id) ON DELETE CASCADE,
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    location        GEOMETRY(Point, 4326) NOT NULL,
    accuracy_m      NUMERIC(6,2),
    speed_kmh       NUMERIC(5,2),
    heading_deg     NUMERIC(5,2),
    altitude_m      NUMERIC(7,2),
    battery_pct     SMALLINT,
    recorded_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
) PARTITION BY RANGE (recorded_at);

-- Create initial time partitions (monthly)
CREATE TABLE live_gps_telemetry_2026_08
    PARTITION OF live_gps_telemetry
    FOR VALUES FROM ('2026-08-01') TO ('2026-09-01');

CREATE TABLE live_gps_telemetry_2026_09
    PARTITION OF live_gps_telemetry
    FOR VALUES FROM ('2026-09-01') TO ('2026-10-01');

CREATE TABLE live_gps_telemetry_2026_10
    PARTITION OF live_gps_telemetry
    FOR VALUES FROM ('2026-10-01') TO ('2026-11-01');

-- ============================================================
-- EXPENSE LOGS
-- ============================================================
CREATE TABLE expense_logs (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    trip_id         UUID NOT NULL REFERENCES trips(id) ON DELETE CASCADE,
    leg_id          UUID REFERENCES trip_legs(id) ON DELETE SET NULL,
    amount          NUMERIC(8,2) NOT NULL,
    category        VARCHAR(20) CHECK (category IN ('transit','food','stay','misc')),
    description     TEXT,
    recorded_at     TIMESTAMPTZ DEFAULT NOW()
);
```

---

## File: `infrastructure/docker/postgres/init/03_indexes.sql`

```sql
-- ============================================================
-- SPATIAL INDEXES (GiST for geometry columns)
-- ============================================================
CREATE INDEX idx_trips_origin_gist        ON trips         USING GIST (origin_point);
CREATE INDEX idx_trips_dest_gist          ON trips         USING GIST (dest_point);
CREATE INDEX idx_trip_legs_from_gist      ON trip_legs     USING GIST (from_point);
CREATE INDEX idx_trip_legs_to_gist        ON trip_legs     USING GIST (to_point);
CREATE INDEX idx_trip_legs_route_gist     ON trip_legs     USING GIST (route_line);
CREATE INDEX idx_safety_zones_polygon     ON safety_zones  USING GIST (zone_polygon);
CREATE INDEX idx_crowdsourced_point       ON crowdsourced_reports USING GIST (report_point);
CREATE INDEX idx_telemetry_location       ON live_gps_telemetry   USING GIST (location);
CREATE INDEX idx_stayfood_location        ON stay_and_food_recommendations USING GIST (location);

-- ============================================================
-- STANDARD B-TREE INDEXES
-- ============================================================
CREATE INDEX idx_trips_user_id            ON trips              (user_id);
CREATE INDEX idx_trips_status             ON trips              (status);
CREATE INDEX idx_trip_legs_trip_id        ON trip_legs          (trip_id, leg_order);
CREATE INDEX idx_telemetry_trip_time      ON live_gps_telemetry (trip_id, recorded_at DESC);
CREATE INDEX idx_telemetry_user_time      ON live_gps_telemetry (user_id, recorded_at DESC);
CREATE INDEX idx_reports_expires          ON crowdsourced_reports (expires_at)
                                          WHERE verified = FALSE;
CREATE INDEX idx_safety_zones_city        ON safety_zones        (city, active);
CREATE INDEX idx_safety_zones_risk        ON safety_zones        (risk_score) WHERE active = TRUE;
```

---

## File: `infrastructure/docker/postgres/init/04_functions.sql`

```sql
-- ============================================================
-- FUNCTION: Check if a point is inside a high-risk zone
-- Returns zone info if risk_score >= threshold (default 3)
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
DECLARE
    v_point GEOMETRY;
BEGIN
    v_point := ST_SetSRID(ST_MakePoint(p_lon, p_lat), 4326);
    RETURN QUERY
    SELECT
        sz.id,
        sz.name,
        CASE WHEN p_night_mode AND sz.time_sensitive THEN sz.night_risk_score
             ELSE sz.risk_score END AS effective_risk,
        sz.risk_factors
    FROM safety_zones sz
    WHERE sz.active = TRUE
      AND ST_Contains(sz.zone_polygon, v_point)
      AND (
            CASE WHEN p_night_mode AND sz.time_sensitive THEN sz.night_risk_score
                 ELSE sz.risk_score END
          ) >= p_min_risk
    ORDER BY effective_risk DESC;
END;
$$ LANGUAGE plpgsql STABLE;

-- ============================================================
-- FUNCTION: Find nearby safe zones / refuges within radius
-- p_radius_m: search radius in metres
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
DECLARE
    v_point GEOMETRY;
BEGIN
    v_point := ST_SetSRID(ST_MakePoint(p_lon, p_lat), 4326);
    RETURN QUERY
    SELECT
        sz.id,
        sz.name,
        sz.risk_score,
        ST_Distance(
            ST_Centroid(sz.zone_polygon)::GEOGRAPHY,
            v_point::GEOGRAPHY
        ) AS distance_m
    FROM safety_zones sz
    WHERE sz.active = TRUE
      AND sz.risk_score <= p_max_risk
      AND ST_DWithin(
            ST_Centroid(sz.zone_polygon)::GEOGRAPHY,
            v_point::GEOGRAPHY,
            p_radius_m
          )
    ORDER BY distance_m ASC
    LIMIT 10;
END;
$$ LANGUAGE plpgsql STABLE;

-- ============================================================
-- FUNCTION: Detect route deviation for a trip
-- Returns TRUE if user is >300m from any point on the route
-- ============================================================
CREATE OR REPLACE FUNCTION fn_is_off_route(
    p_trip_id    UUID,
    p_lat        DOUBLE PRECISION,
    p_lon        DOUBLE PRECISION,
    p_threshold_m DOUBLE PRECISION DEFAULT 300.0
)
RETURNS BOOLEAN AS $$
DECLARE
    v_point     GEOMETRY;
    v_min_dist  DOUBLE PRECISION;
BEGIN
    v_point := ST_SetSRID(ST_MakePoint(p_lon, p_lat), 4326);
    SELECT MIN(
        ST_Distance(tl.route_line::GEOGRAPHY, v_point::GEOGRAPHY)
    )
    INTO v_min_dist
    FROM trip_legs tl
    WHERE tl.trip_id = p_trip_id
      AND tl.status = 'in_progress'
      AND tl.route_line IS NOT NULL;

    RETURN COALESCE(v_min_dist, 0) > p_threshold_m;
END;
$$ LANGUAGE plpgsql STABLE;

-- ============================================================
-- FUNCTION: Get nearby crowdsourced alerts
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
    created_at  TIMESTAMPTZ
) AS $$
DECLARE
    v_point GEOMETRY;
BEGIN
    v_point := ST_SetSRID(ST_MakePoint(p_lon, p_lat), 4326);
    RETURN QUERY
    SELECT
        cr.id,
        cr.category,
        cr.severity,
        ST_Distance(cr.report_point::GEOGRAPHY, v_point::GEOGRAPHY) AS distance_m,
        cr.description,
        cr.created_at
    FROM crowdsourced_reports cr
    WHERE cr.expires_at > NOW()
      AND cr.downvotes < 5
      AND ST_DWithin(cr.report_point::GEOGRAPHY, v_point::GEOGRAPHY, p_radius_m)
    ORDER BY cr.severity DESC, distance_m ASC
    LIMIT 20;
END;
$$ LANGUAGE plpgsql STABLE;
```

---

## File: `infrastructure/docker/postgres/init/05_seed.sql`

```sql
-- ============================================================
-- SEED: Verified Safety Zones — Delhi NCR
-- ============================================================
INSERT INTO safety_zones (name, city, zone_polygon, risk_score, risk_factors, time_sensitive, night_risk_score, verified_by) VALUES

-- Connaught Place (relatively safe, commercial)
('Connaught Place', 'Delhi',
 ST_GeomFromText('POLYGON((77.2155 28.6330, 77.2255 28.6330, 77.2255 28.6280, 77.2155 28.6280, 77.2155 28.6330))', 4326),
 1, ARRAY['well_lit','commercial','police_present'], TRUE, 2, 'admin'),

-- Paharganj (budget area, moderate risk)
('Paharganj Main Bazar', 'Delhi',
 ST_GeomFromText('POLYGON((77.2090 28.6430, 77.2160 28.6430, 77.2160 28.6370, 77.2090 28.6370, 77.2090 28.6430))', 4326),
 3, ARRAY['crowded','pickpocket_risk','poor_lighting_alleys'], TRUE, 4, 'admin'),

-- Hazrat Nizamuddin Station (high risk at night)
('Nizamuddin Railway Station Surroundings', 'Delhi',
 ST_GeomFromText('POLYGON((77.2490 28.5880, 77.2570 28.5880, 77.2570 28.5820, 77.2490 28.5820, 77.2490 28.5880))', 4326),
 2, ARRAY['railway_station'], TRUE, 4, 'admin'),

-- Saket Metro (safe commercial zone)
('Saket District Centre', 'Delhi',
 ST_GeomFromText('POLYGON((77.2130 28.5240, 77.2230 28.5240, 77.2230 28.5160, 77.2130 28.5160, 77.2130 28.5240))', 4326),
 1, ARRAY['mall_zone','well_lit','cctv'], FALSE, NULL, 'admin'),

-- Outer Ring Road stretch (moderate night risk)
('Outer Ring Road — Lajpat to Moolchand', 'Delhi',
 ST_GeomFromText('POLYGON((77.2350 28.5680, 77.2500 28.5680, 77.2500 28.5580, 77.2350 28.5580, 77.2350 28.5680))', 4326),
 2, ARRAY['highway_stretch'], TRUE, 3, 'ml');

-- ============================================================
-- SEED: Verified Safety Zones — Mumbai
-- ============================================================
INSERT INTO safety_zones (name, city, zone_polygon, risk_score, risk_factors, time_sensitive, night_risk_score, verified_by) VALUES

-- Dharavi (high risk)
('Dharavi', 'Mumbai',
 ST_GeomFromText('POLYGON((72.8480 19.0420, 72.8600 19.0420, 72.8600 19.0310, 72.8480 19.0310, 72.8480 19.0420))', 4326),
 4, ARRAY['dense_population','crime_reports','poor_infrastructure'], TRUE, 5, 'admin'),

-- Bandra (safe commercial)
('Bandra West Linking Road', 'Mumbai',
 ST_GeomFromText('POLYGON((72.8260 19.0640, 72.8360 19.0640, 72.8360 19.0560, 72.8260 19.0560, 72.8260 19.0640))', 4326),
 1, ARRAY['commercial','well_lit','police_naka'], FALSE, NULL, 'admin'),

-- Kurla Station (high risk at night)
('Kurla Railway Station Area', 'Mumbai',
 ST_GeomFromText('POLYGON((72.8780 19.0690, 72.8870 19.0690, 72.8870 19.0620, 72.8780 19.0620, 72.8780 19.0690))', 4326),
 3, ARRAY['overcrowded','pickpocket_risk'], TRUE, 4, 'admin');

-- ============================================================
-- SEED: Stay & Food (budget-friendly, women-friendly)
-- ============================================================
INSERT INTO stay_and_food_recommendations
  (category, name, location, address, price_per_unit, rating, safety_verified, women_friendly, tags, source)
VALUES
('stay', 'Zostel Delhi', ST_GeomFromText('POINT(77.2090 28.6441)', 4326),
 'Arakashan Road, Paharganj, New Delhi', 650, 4.2, TRUE, TRUE,
 ARRAY['hostel','budget','wifi','locker','24hr'], 'manual'),

('stay', 'Moustache Hostel Jaipur', ST_GeomFromText('POINT(75.8267 26.9124)', 4326),
 'D-160, Devi Marg, Bani Park, Jaipur', 550, 4.5, TRUE, TRUE,
 ARRAY['hostel','budget','rooftop','social'], 'manual'),

('food', 'Sagar Ratna — CP', ST_GeomFromText('POINT(77.2197 28.6310)', 4326),
 'Block E, Connaught Place, New Delhi', 180, 4.0, TRUE, TRUE,
 ARRAY['veg','thali','budget','clean'], 'manual'),

('food', 'Indian Coffee House — CP', ST_GeomFromText('POINT(77.2184 28.6322)', 4326),
 'Mohan Singh Place, Baba Kharak Singh Marg, New Delhi', 80, 3.8, TRUE, FALSE,
 ARRAY['veg','budget','heritage'], 'manual');
```
