-- Migration 004: Create hpd_registrations table (TES-53)
-- Run once in the Supabase SQL Editor.
-- Safe to re-run — all statements use IF NOT EXISTS.
--
-- Creates:
--   hpd_registrations   Building owner / managing-agent contacts from the
--                       NYC HPD Building Registration database.
--
-- Data source: NYC Open Data dataset mnqr-ivp3
--   (HPD Multiple Dwelling Registrations)

CREATE TABLE IF NOT EXISTS hpd_registrations (
    id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    property_id           UUID NOT NULL REFERENCES properties(id) ON DELETE CASCADE,
    registration_id       TEXT NOT NULL,   -- HPD registration ID
    lifecycle_stage       TEXT,            -- e.g. 'Active', 'Terminated'
    owner_name            TEXT,
    owner_type            TEXT,            -- e.g. 'Individual', 'Corporation'
    contact_name          TEXT,            -- managing agent or owner contact
    contact_type          TEXT,            -- e.g. 'HeadOfficer', 'Agent'
    registration_end_date DATE,
    created_at            TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (property_id, registration_id)  -- idempotent upserts
);

CREATE INDEX IF NOT EXISTS idx_hpd_registrations_property_id ON hpd_registrations(property_id);
CREATE INDEX IF NOT EXISTS idx_hpd_registrations_reg_id      ON hpd_registrations(registration_id);

-- Verify
SELECT column_name, data_type
FROM information_schema.columns
WHERE table_name = 'hpd_registrations'
ORDER BY ordinal_position;
