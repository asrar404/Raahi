-- ============================================================
-- RAAHI — 01: Required PostgreSQL Extensions
-- Runs first (docker-entrypoint-initdb.d executes in name order).
-- ============================================================

-- Spatial types, indexes and functions (ST_Contains, ST_DWithin, ...)
CREATE EXTENSION IF NOT EXISTS postgis;

-- Topology support (network/edge modelling for future transit graphs)
CREATE EXTENSION IF NOT EXISTS postgis_topology;

-- uuid_generate_v4() for primary keys
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Trigram matching for fuzzy place-name search ("paharganj" ~ "Pahar Ganj")
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- Composite B-tree/GiST indexes on (scalar, geometry) pairs
CREATE EXTENSION IF NOT EXISTS btree_gist;

-- Report versions into the log so init problems are obvious on first boot
DO $$
BEGIN
    RAISE NOTICE 'PostGIS version: %', postgis_version();
END
$$;
