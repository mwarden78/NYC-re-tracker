-- Migration 006: Create complaints_311 table (TES-55)
-- Run once in the Supabase SQL Editor.
-- Safe to re-run — all statements use IF NOT EXISTS.
--
-- Creates:
--   complaints_311   311 service requests linked to properties, filtered to
--                    address-level complaints (HPD, DOB, NYPD, etc.).
--
-- Data source: NYC Open Data dataset erm2-nwe9
--   (311 Service Requests from 2010 to Present)

CREATE TABLE IF NOT EXISTS complaints_311 (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    property_id    UUID NOT NULL REFERENCES properties(id) ON DELETE CASCADE,
    external_id    TEXT NOT NULL,    -- 311 unique_key field
    complaint_type TEXT,             -- e.g. 'HEAT/HOT WATER', 'NOISE - RESIDENTIAL'
    descriptor     TEXT,             -- sub-type of the complaint
    status         TEXT,             -- e.g. 'Open', 'Closed', 'In Progress'
    agency         TEXT,             -- responding agency: HPD, NYPD, DOB, etc.
    created_date   DATE,
    closed_date    DATE,
    created_at     TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (property_id, external_id)  -- idempotent upserts
);

CREATE INDEX IF NOT EXISTS idx_complaints_311_property_id    ON complaints_311(property_id);
CREATE INDEX IF NOT EXISTS idx_complaints_311_created_date   ON complaints_311(created_date DESC);
CREATE INDEX IF NOT EXISTS idx_complaints_311_complaint_type ON complaints_311(complaint_type);

-- Verify
SELECT column_name, data_type
FROM information_schema.columns
WHERE table_name = 'complaints_311'
ORDER BY ordinal_position;
