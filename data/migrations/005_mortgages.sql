-- Migration 005: Create mortgages table + add columns to properties (TES-54)
-- Run once in the Supabase SQL Editor.
-- Safe to re-run — all statements use IF NOT EXISTS.
--
-- Creates:
--   mortgages   ACRIS mortgage records (MTGE, AGMT, ASST, CORR, MMOD, SMOD, SMXT)
--               linked to properties via property_id and bbl.
--
-- Also adds two summary columns to properties:
--   active_mortgage_amount  NUMERIC
--   active_mortgage_lender  TEXT
--
-- Data sources: NYC Open Data ACRIS
--   Master:  bnx9-e6tj
--   Legals:  8h5j-fqxa
--   Parties: 636b-3b5g

CREATE TABLE IF NOT EXISTS mortgages (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    property_id      UUID NOT NULL REFERENCES properties(id) ON DELETE CASCADE,
    document_id      TEXT NOT NULL,      -- ACRIS 16-char document ID
    doc_type         TEXT,               -- e.g. 'MTGE', 'AGMT', 'ASST'
    lender_name      TEXT,               -- ACRIS Parties party_type='2'
    mortgage_amount  NUMERIC,            -- document_amt from ACRIS Master
    mortgage_date    DATE,               -- document_date
    maturity_date    DATE,               -- good_thru_date from ACRIS Master
    recorded_at      TIMESTAMPTZ,        -- recorded_datetime from ACRIS Master
    bbl              TEXT,
    created_at       TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (property_id, document_id)    -- idempotent upserts
);

CREATE INDEX IF NOT EXISTS idx_mortgages_property_id  ON mortgages(property_id);
CREATE INDEX IF NOT EXISTS idx_mortgages_mortgage_date ON mortgages(mortgage_date DESC);
CREATE INDEX IF NOT EXISTS idx_mortgages_bbl           ON mortgages(bbl);

-- Summary columns on properties (populated by enrich_acris_mortgages.py)
ALTER TABLE properties
    ADD COLUMN IF NOT EXISTS active_mortgage_amount NUMERIC,
    ADD COLUMN IF NOT EXISTS active_mortgage_lender TEXT;

-- Verify
SELECT column_name, data_type
FROM information_schema.columns
WHERE table_name = 'mortgages'
ORDER BY ordinal_position;
