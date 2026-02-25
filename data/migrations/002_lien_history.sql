-- Migration 002: Create lien_history table for prior tax lien records
-- Run this once in the Supabase SQL Editor.
-- Safe to re-run — all statements use IF NOT EXISTS.
--
-- Creates:
--   lien_history   Stores each DOF tax lien notice/sale record keyed to a property via BBL.
--                  Populated by data/ingest_lien_history.py
--
-- Data source: NYC Open Data dataset 9rz4-mjek
--   (NYC-DOF Tax Lien Sale – Tax Lien List)

CREATE TABLE IF NOT EXISTS lien_history (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    property_id     UUID NOT NULL REFERENCES properties(id) ON DELETE CASCADE,
    bbl             TEXT NOT NULL,               -- 10-digit BBL (B + BLOCK5 + LOT4)
    tax_class       TEXT,                        -- tax class code: '1', '2', '3', '4'
    building_class  TEXT,                        -- DOF building class, e.g. 'A1', 'D4'
    lien_cycle      TEXT,                        -- e.g. '90 Day Notice', 'In Rem'
    water_debt_only BOOLEAN DEFAULT FALSE,       -- true when lien is water-debt only
    lien_amount     NUMERIC,                     -- total lien amount (if available)
    notice_month    TEXT,                        -- YYYY-MM-DD from the DOF 'month' field
    source_row_id   TEXT,                        -- stable row identifier for dedup
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (property_id, source_row_id)          -- idempotent upserts
);

CREATE INDEX IF NOT EXISTS idx_lien_history_property_id ON lien_history(property_id);
CREATE INDEX IF NOT EXISTS idx_lien_history_bbl         ON lien_history(bbl);
CREATE INDEX IF NOT EXISTS idx_lien_history_notice_month ON lien_history(notice_month DESC);

-- Verify: confirm table was created with expected columns
SELECT column_name, data_type
FROM information_schema.columns
WHERE table_name = 'lien_history'
ORDER BY ordinal_position;
