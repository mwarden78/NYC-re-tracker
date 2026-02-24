-- Migration 001: Add BBL and PLUTO enrichment columns to properties table
-- Run this once in the Supabase SQL Editor.
-- Safe to re-run — all statements use IF NOT EXISTS / IF NOT EXISTS equivalents.
--
-- Adds:
--   bbl              TEXT       10-digit NYC parcel ID (join key for PLUTO)
--   assessed_value   NUMERIC    DOF total assessed value
--   market_value     NUMERIC    DOF full market value
--   num_units        INTEGER    residential unit count (PLUTO unitsres)
--   num_floors       INTEGER    number of floors (PLUTO numfloors)
--   land_use         TEXT       land use code + label, e.g. '02 - Two-Family Buildings'
--   zoning_district  TEXT       primary zoning district, e.g. 'R6', 'M1-2'
--   last_sale_price  NUMERIC    most recent ACRIS DEED sale price
--   last_sale_date   DATE       most recent ACRIS DEED sale date

ALTER TABLE properties
    ADD COLUMN IF NOT EXISTS bbl              TEXT,
    ADD COLUMN IF NOT EXISTS assessed_value   NUMERIC,
    ADD COLUMN IF NOT EXISTS market_value     NUMERIC,
    ADD COLUMN IF NOT EXISTS num_units        INTEGER,
    ADD COLUMN IF NOT EXISTS num_floors       INTEGER,
    ADD COLUMN IF NOT EXISTS land_use         TEXT,
    ADD COLUMN IF NOT EXISTS zoning_district  TEXT,
    ADD COLUMN IF NOT EXISTS last_sale_price  NUMERIC,
    ADD COLUMN IF NOT EXISTS last_sale_date   DATE;

-- Index on bbl for fast PLUTO join lookups
CREATE INDEX IF NOT EXISTS idx_properties_bbl ON properties(bbl);

-- Verify: list the new columns
SELECT column_name, data_type
FROM information_schema.columns
WHERE table_name = 'properties'
  AND column_name IN (
      'bbl', 'assessed_value', 'market_value',
      'num_units', 'num_floors', 'land_use',
      'zoning_district', 'last_sale_price', 'last_sale_date'
  )
ORDER BY column_name;
