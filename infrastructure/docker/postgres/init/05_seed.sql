-- ============================================================
-- RAAHI — 05: Seed Data
--
-- Approximate real-world coordinates for Delhi NCR, Mumbai and Jaipur.
-- risk_score: 1 = very safe ... 5 = very high risk
-- Zones marked time_sensitive use night_risk_score after 22:00 IST.
--
-- These are illustrative development fixtures for demoing the safety
-- engine, not an authoritative or endorsed assessment of any locality.
-- Production data should come from verified partners and moderated
-- community reports.
-- ============================================================

-- ============================================================
-- SAFETY ZONES — Delhi NCR
-- ============================================================
INSERT INTO safety_zones
    (name, city, zone_polygon, risk_score, risk_factors, time_sensitive, night_risk_score, verified_by)
VALUES
-- Commercial core, heavy policing, well lit
('Connaught Place', 'Delhi',
 ST_GeomFromText('POLYGON((77.2155 28.6330, 77.2255 28.6330, 77.2255 28.6280, 77.2155 28.6280, 77.2155 28.6330))', 4326),
 1, ARRAY['well_lit', 'commercial', 'police_present'], TRUE, 2, 'admin'),

-- Backpacker hub: crowded by day, poorly lit alleys after dark
('Paharganj Main Bazar', 'Delhi',
 ST_GeomFromText('POLYGON((77.2090 28.6430, 77.2160 28.6430, 77.2160 28.6370, 77.2090 28.6370, 77.2090 28.6430))', 4326),
 3, ARRAY['crowded', 'pickpocket_risk', 'poor_lighting_alleys'], TRUE, 4, 'admin'),

-- Major rail terminus, sharp night-time risk climb
('Nizamuddin Railway Station Surroundings', 'Delhi',
 ST_GeomFromText('POLYGON((77.2490 28.5880, 77.2570 28.5880, 77.2570 28.5820, 77.2490 28.5820, 77.2490 28.5880))', 4326),
 2, ARRAY['railway_station', 'transient_crowd'], TRUE, 4, 'admin'),

-- Mall district: CCTV, guards, consistently safe
('Saket District Centre', 'Delhi',
 ST_GeomFromText('POLYGON((77.2130 28.5240, 77.2230 28.5240, 77.2230 28.5160, 77.2130 28.5160, 77.2130 28.5240))', 4326),
 1, ARRAY['mall_zone', 'well_lit', 'cctv'], FALSE, NULL, 'admin'),

-- Fast arterial road, few pedestrians late at night
('Outer Ring Road - Lajpat to Moolchand', 'Delhi',
 ST_GeomFromText('POLYGON((77.2350 28.5680, 77.2500 28.5680, 77.2500 28.5580, 77.2350 28.5580, 77.2350 28.5680))', 4326),
 2, ARRAY['highway_stretch', 'sparse_footfall'], TRUE, 3, 'ml'),

-- Student area, busy and generally safe until late
('Hauz Khas Village', 'Delhi',
 ST_GeomFromText('POLYGON((77.1930 28.5560, 77.2010 28.5560, 77.2010 28.5490, 77.1930 28.5490, 77.1930 28.5560))', 4326),
 2, ARRAY['nightlife', 'narrow_lanes'], TRUE, 3, 'community'),

-- Interstate bus terminal, high churn
('Kashmere Gate ISBT', 'Delhi',
 ST_GeomFromText('POLYGON((77.2270 28.6690, 77.2350 28.6690, 77.2350 28.6620, 77.2270 28.6620, 77.2270 28.6690))', 4326),
 3, ARRAY['bus_terminal', 'transient_crowd', 'pickpocket_risk'], TRUE, 4, 'admin'),

-- Planned business district, patrolled, wide roads
('Cyber Hub Gurugram', 'Gurugram',
 ST_GeomFromText('POLYGON((77.0870 28.4980, 77.0960 28.4980, 77.0960 28.4910, 77.0870 28.4910, 77.0870 28.4980))', 4326),
 1, ARRAY['corporate_zone', 'well_lit', 'cctv', 'police_present'], FALSE, NULL, 'admin');

-- ============================================================
-- SAFETY ZONES — Mumbai
-- ============================================================
INSERT INTO safety_zones
    (name, city, zone_polygon, risk_score, risk_factors, time_sensitive, night_risk_score, verified_by)
VALUES
-- Extremely dense settlement, limited street lighting
('Dharavi', 'Mumbai',
 ST_GeomFromText('POLYGON((72.8480 19.0420, 72.8600 19.0420, 72.8600 19.0310, 72.8480 19.0310, 72.8480 19.0420))', 4326),
 4, ARRAY['dense_population', 'crime_reports', 'poor_infrastructure'], TRUE, 5, 'admin'),

-- Retail strip, busy and well policed
('Bandra West Linking Road', 'Mumbai',
 ST_GeomFromText('POLYGON((72.8260 19.0640, 72.8360 19.0640, 72.8360 19.0560, 72.8260 19.0560, 72.8260 19.0640))', 4326),
 1, ARRAY['commercial', 'well_lit', 'police_naka'], FALSE, NULL, 'admin'),

-- Overcrowded junction station
('Kurla Railway Station Area', 'Mumbai',
 ST_GeomFromText('POLYGON((72.8780 19.0690, 72.8870 19.0690, 72.8870 19.0620, 72.8780 19.0620, 72.8780 19.0690))', 4326),
 3, ARRAY['overcrowded', 'pickpocket_risk'], TRUE, 4, 'admin'),

-- Heritage precinct, tourist police presence
('Colaba Causeway', 'Mumbai',
 ST_GeomFromText('POLYGON((72.8280 18.9230, 72.8350 18.9230, 72.8350 18.9150, 72.8280 18.9150, 72.8280 18.9230))', 4326),
 1, ARRAY['tourist_zone', 'well_lit', 'police_present'], TRUE, 2, 'admin'),

-- Business district, quiet and isolated after office hours
('Bandra Kurla Complex', 'Mumbai',
 ST_GeomFromText('POLYGON((72.8630 19.0700, 72.8730 19.0700, 72.8730 19.0600, 72.8630 19.0600, 72.8630 19.0700))', 4326),
 1, ARRAY['corporate_zone', 'cctv'], TRUE, 3, 'ml');

-- ============================================================
-- SAFETY ZONES — Jaipur
-- ============================================================
INSERT INTO safety_zones
    (name, city, zone_polygon, risk_score, risk_factors, time_sensitive, night_risk_score, verified_by)
VALUES
('Bani Park', 'Jaipur',
 ST_GeomFromText('POLYGON((75.8210 26.9160, 75.8320 26.9160, 75.8320 26.9080, 75.8210 26.9080, 75.8210 26.9160))', 4326),
 1, ARRAY['residential', 'guesthouse_district', 'well_lit'], FALSE, NULL, 'admin'),

('Jaipur Walled City Bazaars', 'Jaipur',
 ST_GeomFromText('POLYGON((75.8180 26.9280, 75.8320 26.9280, 75.8320 26.9180, 75.8180 26.9180, 75.8180 26.9280))', 4326),
 2, ARRAY['crowded', 'tourist_zone', 'touts'], TRUE, 3, 'community');

-- ============================================================
-- STAY & FOOD — budget-first, women-friendly where verified
-- ============================================================
INSERT INTO stay_and_food_recommendations
    (category, name, location, address, city, price_per_unit, rating,
     safety_verified, women_friendly, tags, source)
VALUES
('stay', 'Zostel Delhi',
 ST_GeomFromText('POINT(77.2090 28.6441)', 4326),
 'Arakashan Road, Paharganj, New Delhi', 'Delhi',
 650, 4.2, TRUE, TRUE,
 ARRAY['hostel', 'budget', 'wifi', 'locker', '24hr', 'female_dorm'], 'manual'),

('stay', 'Moustache Hostel Jaipur',
 ST_GeomFromText('POINT(75.8267 26.9124)', 4326),
 'D-160, Devi Marg, Bani Park, Jaipur', 'Jaipur',
 550, 4.5, TRUE, TRUE,
 ARRAY['hostel', 'budget', 'rooftop', 'social', 'female_dorm'], 'manual'),

('stay', 'Backpacker Panda Colaba',
 ST_GeomFromText('POINT(72.8317 18.9180)', 4326),
 'Colaba Causeway, Mumbai', 'Mumbai',
 700, 4.0, TRUE, TRUE,
 ARRAY['hostel', 'budget', 'wifi', 'locker'], 'manual'),

('food', 'Sagar Ratna - CP',
 ST_GeomFromText('POINT(77.2197 28.6310)', 4326),
 'Block E, Connaught Place, New Delhi', 'Delhi',
 180, 4.0, TRUE, TRUE,
 ARRAY['veg', 'thali', 'budget', 'clean'], 'manual'),

('food', 'Indian Coffee House - CP',
 ST_GeomFromText('POINT(77.2184 28.6322)', 4326),
 'Mohan Singh Place, Baba Kharak Singh Marg, New Delhi', 'Delhi',
 80, 3.8, TRUE, FALSE,
 ARRAY['veg', 'budget', 'heritage'], 'manual'),

('food', 'Kake Di Hatti - Chandni Chowk',
 ST_GeomFromText('POINT(77.2200 28.6560)', 4326),
 'Fatehpuri, Chandni Chowk, New Delhi', 'Delhi',
 150, 4.1, FALSE, FALSE,
 ARRAY['veg', 'paratha', 'budget', 'crowded'], 'manual'),

('food', 'Cafe Madras - Matunga',
 ST_GeomFromText('POINT(72.8500 19.0270)', 4326),
 'Kings Circle, Matunga East, Mumbai', 'Mumbai',
 160, 4.4, TRUE, TRUE,
 ARRAY['veg', 'south_indian', 'budget', 'breakfast'], 'manual'),

('food', 'Rawat Mishthan Bhandar',
 ST_GeomFromText('POINT(75.8060 26.9210)', 4326),
 'Station Road, Sindhi Camp, Jaipur', 'Jaipur',
 120, 4.3, TRUE, TRUE,
 ARRAY['veg', 'kachori', 'budget', 'iconic'], 'manual');

-- ============================================================
-- DEMO USER
-- supabase_uid is a placeholder; the real value arrives on first login
-- and /auth/verify upserts against it.
-- ============================================================
INSERT INTO users
    (id, supabase_uid, full_name, phone, email, gender,
     preferred_modes, budget_default, emergency_contacts, sos_enabled, home_city)
VALUES (
    '00000000-0000-0000-0000-000000000001',
    'demo-supabase-uid-0001',
    'Demo Traveller',
    '+919999900001',
    'demo@raahi.app',
    'female',
    ARRAY['metro', 'bus', 'auto'],
    500.00,
    '[
        {"name": "Aarti (Sister)",  "phone": "+919999900002", "relation": "sibling"},
        {"name": "Vikram (Friend)", "phone": "+919999900003", "relation": "friend"}
    ]'::JSONB,
    TRUE,
    'Delhi'
) ON CONFLICT (supabase_uid) DO NOTHING;

-- ============================================================
-- SAMPLE CROWDSOURCED REPORTS
-- Long expiry so the heatmap and safety scorer have data to show
-- during development.
-- ============================================================
INSERT INTO crowdsourced_reports
    (user_id, report_point, category, description, severity, verified, upvotes, expires_at)
VALUES
('00000000-0000-0000-0000-000000000001',
 ST_GeomFromText('POINT(77.2120 28.6400)', 4326),
 'poor_lighting', 'Streetlights out along the side lane near the main bazar', 3, TRUE, 12,
 NOW() + INTERVAL '30 days'),

('00000000-0000-0000-0000-000000000001',
 ST_GeomFromText('POINT(77.2145 28.6395)', 4326),
 'harassment', 'Group loitering and catcalling after 22:00', 4, TRUE, 27,
 NOW() + INTERVAL '30 days'),

('00000000-0000-0000-0000-000000000001',
 ST_GeomFromText('POINT(77.2200 28.6305)', 4326),
 'police_present', 'Permanent police booth, staffed 24x7', 1, TRUE, 40,
 NOW() + INTERVAL '90 days'),

('00000000-0000-0000-0000-000000000001',
 ST_GeomFromText('POINT(77.2180 28.5200)', 4326),
 'safe_spot', 'Mall entrance with guards and CCTV, open till 23:00', 1, TRUE, 18,
 NOW() + INTERVAL '90 days'),

('00000000-0000-0000-0000-000000000001',
 ST_GeomFromText('POINT(72.8540 19.0370)', 4326),
 'unsafe_area', 'Avoid the narrow connecting lanes at night', 4, TRUE, 33,
 NOW() + INTERVAL '30 days'),

('00000000-0000-0000-0000-000000000001',
 ST_GeomFromText('POINT(72.8820 19.0655)', 4326),
 'theft', 'Phone snatching reported on the station foot overbridge', 4, TRUE, 21,
 NOW() + INTERVAL '30 days');

-- ============================================================
-- Ensure the partition for the current month exists even if the
-- hardcoded set in 02_schema.sql has aged out.
-- ============================================================
SELECT fn_ensure_telemetry_partition(CURRENT_DATE);
SELECT fn_ensure_telemetry_partition((CURRENT_DATE + INTERVAL '1 month')::DATE);

-- ============================================================
-- Sanity summary in the init log
-- ============================================================
DO $$
DECLARE
    v_zones INT;
    v_places INT;
    v_reports INT;
BEGIN
    SELECT COUNT(*) INTO v_zones   FROM safety_zones;
    SELECT COUNT(*) INTO v_places  FROM stay_and_food_recommendations;
    SELECT COUNT(*) INTO v_reports FROM crowdsourced_reports;
    RAISE NOTICE 'RAAHI seed complete: % safety zones, % places, % reports',
        v_zones, v_places, v_reports;
END
$$;
