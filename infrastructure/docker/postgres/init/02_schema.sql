-- ============================================================
-- RAAHI — 02: Core Schema
-- All geometry columns use SRID 4326 (WGS84 lat/lon).
-- Distance maths casts to GEOGRAPHY so results are in metres.
-- ============================================================

-- ============================================================
-- USERS
-- ============================================================
CREATE TABLE users (
    id                 UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    supabase_uid       TEXT UNIQUE NOT NULL,
    full_name          TEXT NOT NULL,
    phone              VARCHAR(15) UNIQUE NOT NULL,
    email              TEXT UNIQUE,
    gender             VARCHAR(10) CHECK (gender IN ('female', 'male', 'other')),
    preferred_modes    TEXT[] NOT NULL DEFAULT ARRAY['metro', 'bus', 'auto'],
    budget_default     NUMERIC(10,2) NOT NULL DEFAULT 500.00
                           CHECK (budget_default >= 0),
    -- [{ "name": "...", "phone": "+91...", "relation": "..." }, ...]
    emergency_contacts JSONB NOT NULL DEFAULT '[]'::JSONB
                           CHECK (jsonb_typeof(emergency_contacts) = 'array'),
    sos_enabled        BOOLEAN NOT NULL DEFAULT TRUE,
    home_city          TEXT,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON COLUMN users.supabase_uid IS 'Subject claim (sub) from the Supabase JWT';
COMMENT ON COLUMN users.emergency_contacts IS 'JSONB array of {name, phone, relation}';

-- ============================================================
-- TRIPS
-- ============================================================
CREATE TABLE trips (
    id                 UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id            UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    status             VARCHAR(20) NOT NULL DEFAULT 'planned'
                           CHECK (status IN ('planned', 'active', 'completed', 'cancelled', 'sos')),
    origin_name        TEXT NOT NULL,
    origin_point       GEOMETRY(Point, 4326) NOT NULL,
    dest_name          TEXT NOT NULL,
    dest_point         GEOMETRY(Point, 4326) NOT NULL,
    budget_ceiling     NUMERIC(10,2) NOT NULL CHECK (budget_ceiling > 0),
    time_deadline      TIMESTAMPTZ,
    transit_prefs      TEXT[] NOT NULL DEFAULT ARRAY['metro', 'bus', 'auto', 'cab'],
    total_planned_cost NUMERIC(10,2) CHECK (total_planned_cost >= 0),
    total_actual_cost  NUMERIC(10,2) NOT NULL DEFAULT 0 CHECK (total_actual_cost >= 0),
    planned_eta        TIMESTAMPTZ,
    actual_eta         TIMESTAMPTZ,
    utility_score      NUMERIC(5,4) CHECK (utility_score BETWEEN 0 AND 1),
    safety_priority    BOOLEAN NOT NULL DEFAULT TRUE,
    -- Verbatim natural-language request, kept for reroute context + auditing
    raw_intent         TEXT,
    -- Serialised ParsedIntent from the AI engine, replayed on reroute
    intent_json        JSONB NOT NULL DEFAULT '{}'::JSONB,
    started_at         TIMESTAMPTZ,
    completed_at       TIMESTAMPTZ,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- A trip cannot finish before it starts
    CONSTRAINT chk_trip_timeline
        CHECK (completed_at IS NULL OR started_at IS NULL OR completed_at >= started_at)
);

COMMENT ON COLUMN trips.intent_json IS 'ParsedIntent payload replayed by the AI engine on reroute';
COMMENT ON COLUMN trips.utility_score IS 'Normalised 0-1 blend of cost, time and safety';

-- ============================================================
-- TRIP LEGS — individual transit segments, ordered by leg_order
-- ============================================================
CREATE TABLE trip_legs (
    id                    UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    trip_id               UUID NOT NULL REFERENCES trips(id) ON DELETE CASCADE,
    leg_order             SMALLINT NOT NULL CHECK (leg_order >= 0),
    mode                  VARCHAR(20) NOT NULL
                              CHECK (mode IN ('walk', 'metro', 'bus', 'train',
                                              'auto', 'cab', 'rapido', 'ferry')),
    from_name             TEXT NOT NULL,
    from_point            GEOMETRY(Point, 4326) NOT NULL,
    to_name               TEXT NOT NULL,
    to_point              GEOMETRY(Point, 4326) NOT NULL,
    route_line            GEOMETRY(LineString, 4326),
    distance_km           NUMERIC(8,3) CHECK (distance_km >= 0),
    planned_cost          NUMERIC(8,2) NOT NULL DEFAULT 0 CHECK (planned_cost >= 0),
    actual_cost           NUMERIC(8,2) CHECK (actual_cost >= 0),
    planned_duration_mins INT CHECK (planned_duration_mins >= 0),
    actual_duration_mins  INT CHECK (actual_duration_mins >= 0),
    provider              TEXT,
    booking_ref           TEXT,
    status                VARCHAR(20) NOT NULL DEFAULT 'pending'
                              CHECK (status IN ('pending', 'in_progress', 'completed', 'skipped')),
    departed_at           TIMESTAMPTZ,
    arrived_at            TIMESTAMPTZ,
    safety_score          NUMERIC(3,2) CHECK (safety_score BETWEEN 0 AND 5),
    created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- Leg ordering is unique within a trip
    CONSTRAINT uq_trip_leg_order UNIQUE (trip_id, leg_order)
);

COMMENT ON COLUMN trip_legs.route_line IS 'Planned polyline; fn_is_off_route measures deviation against this';
COMMENT ON COLUMN trip_legs.safety_score IS '0 = dangerous, 5 = safest';

-- ============================================================
-- STAY & FOOD RECOMMENDATIONS
-- ============================================================
CREATE TABLE stay_and_food_recommendations (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    trip_id         UUID REFERENCES trips(id) ON DELETE SET NULL,
    category        VARCHAR(10) NOT NULL CHECK (category IN ('stay', 'food')),
    name            TEXT NOT NULL,
    location        GEOMETRY(Point, 4326) NOT NULL,
    address         TEXT,
    city            TEXT,
    price_per_unit  NUMERIC(8,2) CHECK (price_per_unit >= 0),
    rating          NUMERIC(2,1) CHECK (rating BETWEEN 0 AND 5),
    safety_verified BOOLEAN NOT NULL DEFAULT FALSE,
    women_friendly  BOOLEAN NOT NULL DEFAULT FALSE,
    tags            TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
    source          VARCHAR(20) CHECK (source IN ('google', 'zomato', 'booking', 'manual')),
    external_id     TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON COLUMN stay_and_food_recommendations.price_per_unit
    IS 'INR per night for stay, per meal for food';

-- ============================================================
-- SAFETY ZONES — polygon geofences carrying a risk score
-- risk_score: 1 = very safe ... 5 = very high risk
-- ============================================================
CREATE TABLE safety_zones (
    id               UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name             TEXT NOT NULL,
    city             TEXT NOT NULL,
    zone_polygon     GEOMETRY(Polygon, 4326) NOT NULL,
    risk_score       SMALLINT NOT NULL CHECK (risk_score BETWEEN 1 AND 5),
    risk_factors     TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
    -- TRUE when night_risk_score should override risk_score after dark
    time_sensitive   BOOLEAN NOT NULL DEFAULT FALSE,
    night_risk_score SMALLINT CHECK (night_risk_score BETWEEN 1 AND 5),
    verified_by      VARCHAR(20) NOT NULL DEFAULT 'ml'
                         CHECK (verified_by IN ('ml', 'admin', 'community')),
    active           BOOLEAN NOT NULL DEFAULT TRUE,
    last_updated     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- A time-sensitive zone is meaningless without a night score
    CONSTRAINT chk_night_score_present
        CHECK (NOT time_sensitive OR night_risk_score IS NOT NULL),
    -- Reject self-intersecting rings early; ST_Contains misbehaves on them
    CONSTRAINT chk_polygon_valid CHECK (ST_IsValid(zone_polygon))
);

-- ============================================================
-- CROWDSOURCED REPORTS — user-submitted, time-decaying signals
-- ============================================================
CREATE TABLE crowdsourced_reports (
    id           UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id      UUID REFERENCES users(id) ON DELETE SET NULL,
    report_point GEOMETRY(Point, 4326) NOT NULL,
    category     VARCHAR(30) NOT NULL
                     CHECK (category IN (
                         'harassment', 'theft', 'poor_lighting',
                         'unsafe_area', 'accident', 'flooding',
                         'road_blocked', 'safe_spot', 'police_present'
                     )),
    description  TEXT,
    severity     SMALLINT NOT NULL DEFAULT 3 CHECK (severity BETWEEN 1 AND 5),
    verified     BOOLEAN NOT NULL DEFAULT FALSE,
    upvotes      INT NOT NULL DEFAULT 0 CHECK (upvotes >= 0),
    downvotes    INT NOT NULL DEFAULT 0 CHECK (downvotes >= 0),
    expires_at   TIMESTAMPTZ NOT NULL DEFAULT (NOW() + INTERVAL '24 hours'),
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE crowdsourced_reports
    IS 'Reports expire after 24h by default; 5+ downvotes suppresses them';

-- ============================================================
-- LIVE GPS TELEMETRY — append-only hot path, partitioned monthly
--
-- NOTE: a partitioned table's unique/primary key MUST contain every
-- partition-key column, so the PK is (id, recorded_at) rather than id.
-- ============================================================
CREATE TABLE live_gps_telemetry (
    id          BIGINT GENERATED BY DEFAULT AS IDENTITY,
    trip_id     UUID NOT NULL,
    user_id     UUID NOT NULL,
    location    GEOMETRY(Point, 4326) NOT NULL,
    accuracy_m  NUMERIC(6,2) CHECK (accuracy_m >= 0),
    speed_kmh   NUMERIC(5,2) CHECK (speed_kmh >= 0),
    heading_deg NUMERIC(5,2) CHECK (heading_deg BETWEEN 0 AND 360),
    altitude_m  NUMERIC(7,2),
    battery_pct SMALLINT CHECK (battery_pct BETWEEN 0 AND 100),
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    PRIMARY KEY (id, recorded_at),
    -- FKs from a partitioned table are supported on PostgreSQL 12+
    CONSTRAINT fk_telemetry_trip FOREIGN KEY (trip_id) REFERENCES trips(id) ON DELETE CASCADE,
    CONSTRAINT fk_telemetry_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
) PARTITION BY RANGE (recorded_at);

-- Monthly partitions. fn_ensure_telemetry_partition() (04_functions.sql)
-- creates future ones; telemetry_default catches anything unplanned so a
-- missing partition can never drop a live location ping.
CREATE TABLE live_gps_telemetry_2026_08 PARTITION OF live_gps_telemetry
    FOR VALUES FROM ('2026-08-01') TO ('2026-09-01');
CREATE TABLE live_gps_telemetry_2026_09 PARTITION OF live_gps_telemetry
    FOR VALUES FROM ('2026-09-01') TO ('2026-10-01');
CREATE TABLE live_gps_telemetry_2026_10 PARTITION OF live_gps_telemetry
    FOR VALUES FROM ('2026-10-01') TO ('2026-11-01');
CREATE TABLE live_gps_telemetry_2026_11 PARTITION OF live_gps_telemetry
    FOR VALUES FROM ('2026-11-01') TO ('2026-12-01');
CREATE TABLE live_gps_telemetry_2026_12 PARTITION OF live_gps_telemetry
    FOR VALUES FROM ('2026-12-01') TO ('2027-01-01');
CREATE TABLE live_gps_telemetry_default PARTITION OF live_gps_telemetry DEFAULT;

-- ============================================================
-- EXPENSE LOGS — actual spend, drives budget alerts
-- ============================================================
CREATE TABLE expense_logs (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    trip_id     UUID NOT NULL REFERENCES trips(id) ON DELETE CASCADE,
    leg_id      UUID REFERENCES trip_legs(id) ON DELETE SET NULL,
    amount      NUMERIC(8,2) NOT NULL CHECK (amount >= 0),
    category    VARCHAR(20) NOT NULL DEFAULT 'misc'
                    CHECK (category IN ('transit', 'food', 'stay', 'misc')),
    description TEXT,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ============================================================
-- SOS EVENTS — immutable audit trail of every escalation
-- ============================================================
CREATE TABLE sos_events (
    id               UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    trip_id          UUID REFERENCES trips(id) ON DELETE SET NULL,
    user_id          UUID REFERENCES users(id) ON DELETE SET NULL,
    trigger_source   VARCHAR(20) NOT NULL DEFAULT 'auto'
                         CHECK (trigger_source IN ('auto', 'manual', 'watcher')),
    location         GEOMETRY(Point, 4326),
    risk_snapshot    JSONB NOT NULL DEFAULT '{}'::JSONB,
    contacts_alerted JSONB NOT NULL DEFAULT '[]'::JSONB,
    sms_sent         INT NOT NULL DEFAULT 0,
    calls_placed     INT NOT NULL DEFAULT 0,
    resolved         BOOLEAN NOT NULL DEFAULT FALSE,
    resolved_at      TIMESTAMPTZ,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ============================================================
-- TRIGGER: keep users.updated_at honest
-- ============================================================
CREATE OR REPLACE FUNCTION fn_touch_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at := NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_users_touch
    BEFORE UPDATE ON users
    FOR EACH ROW
    EXECUTE FUNCTION fn_touch_updated_at();

-- ============================================================
-- TRIGGER: keep safety_zones.last_updated honest
-- ============================================================
CREATE OR REPLACE FUNCTION fn_touch_last_updated()
RETURNS TRIGGER AS $$
BEGIN
    NEW.last_updated := NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_safety_zones_touch
    BEFORE UPDATE ON safety_zones
    FOR EACH ROW
    EXECUTE FUNCTION fn_touch_last_updated();
