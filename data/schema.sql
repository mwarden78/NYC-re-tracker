-- NYC RE Tracker Database Schema
-- Run this in the Supabase SQL Editor

-- Properties table: stores every deal/listing ingested or added manually
CREATE TABLE IF NOT EXISTS properties (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    address TEXT NOT NULL,
    borough TEXT NOT NULL CHECK (borough IN ('Manhattan', 'Brooklyn', 'Queens', 'Bronx', 'Staten Island')),
    zip_code TEXT,
    property_type TEXT CHECK (property_type IN ('condo', 'co-op', 'townhouse', 'multifamily', '1-4 family', 'land')),
    deal_type TEXT NOT NULL CHECK (deal_type IN ('foreclosure', 'tax_lien', 'listing', 'off_market')),
    price NUMERIC,
    price_per_sqft NUMERIC,
    sqft INTEGER,
    bedrooms INTEGER,
    bathrooms NUMERIC,
    lot_sqft INTEGER,
    year_built INTEGER,
    lat NUMERIC,
    lng NUMERIC,
    source_url TEXT,
    source TEXT,         -- e.g. 'nyc_open_data', 'manual', 'zillow'
    listed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),

    -- BBL (Borough-Block-Lot): 10-digit NYC parcel identifier used to join
    -- against PLUTO and ACRIS. Format: B(1) + BLOCK(5,zero-padded) + LOT(4,zero-padded)
    bbl TEXT,

    -- DOF Tax Lien fields (populated during tax lien ingestion)
    building_class  TEXT,      -- e.g. 'A1', 'D4', 'Z9' (DOF building class)
    block           TEXT,      -- BBL block number from DOF dataset
    lot             TEXT,      -- BBL lot number from DOF dataset
    tax_class_code  TEXT,      -- tax class: '1', '2', '4'
    lien_cycle      TEXT,      -- e.g. '90 Day Notice', 'In Rem'
    water_debt_only BOOLEAN,   -- true when lien is water-debt only

    -- PLUTO-enriched fields (populated by data/enrich_pluto.py)
    assessed_value  NUMERIC,   -- DOF total assessed value
    market_value    NUMERIC,   -- DOF full market value
    num_units       INTEGER,   -- residential units (PLUTO unitsres)
    num_floors      INTEGER,   -- number of floors (PLUTO numfloors)
    land_use        TEXT,      -- PLUTO land use code + label, e.g. '02 - Two-Family Buildings'
    zoning_district TEXT,      -- primary zoning district, e.g. 'R6', 'M1-2'

    -- ACRIS DEED enrichment (populated by data/enrich_last_sale.py)
    last_sale_price NUMERIC,   -- most recent recorded sale price
    last_sale_date  DATE,      -- most recent recorded sale date

    -- ACRIS lien enrichment (populated by data/enrich_lien_amount.py)
    lien_amount     NUMERIC,   -- most recent lien document amount from ACRIS
    lien_doc_type   TEXT,      -- ACRIS doc_type of the matched lien (e.g. 'LIEN', 'LTAX')

    -- Walk Score enrichment (populated by data/enrich_walk_score.py)
    walk_score      INTEGER,   -- Walk Score 0–100 (walkscore.com API)
    transit_score   INTEGER,   -- Transit Score 0–100
    bike_score      INTEGER,   -- Bike Score 0–100

    -- DOF Property Tax Bill enrichment (populated by data/ingest_tax_bills.py)
    -- Source: DOF Property Charges Balance dataset (scjx-j6np), queried by BBL (parid field)
    tax_arrears     NUMERIC,   -- sum of outstanding balances (sum_bal) across all charge records
    annual_tax      NUMERIC,   -- sum of CHG charges (sum_liab) for the most recent tax year
    tax_bill_date   DATE       -- most recent charge update date (up_date)
);

-- Deals table: tracks a user's pipeline status for a property
CREATE TABLE IF NOT EXISTS deals (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    property_id UUID NOT NULL REFERENCES properties(id) ON DELETE CASCADE,
    status TEXT NOT NULL DEFAULT 'watching' CHECK (status IN ('watching', 'analyzing', 'offer_made', 'dead')),
    notes TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Auto-update updated_at on properties
CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER properties_updated_at
    BEFORE UPDATE ON properties
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

CREATE TRIGGER deals_updated_at
    BEFORE UPDATE ON deals
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

-- Migration: if upgrading an existing database, run these to add new columns:
--   ALTER TABLE properties ADD COLUMN IF NOT EXISTS bbl TEXT;
--   CREATE INDEX IF NOT EXISTS idx_properties_bbl ON properties(bbl);
--   ALTER TABLE properties ADD COLUMN IF NOT EXISTS building_class TEXT;
--   ALTER TABLE properties ADD COLUMN IF NOT EXISTS block TEXT;
--   ALTER TABLE properties ADD COLUMN IF NOT EXISTS lot TEXT;
--   ALTER TABLE properties ADD COLUMN IF NOT EXISTS tax_class_code TEXT;
--   ALTER TABLE properties ADD COLUMN IF NOT EXISTS lien_cycle TEXT;
--   ALTER TABLE properties ADD COLUMN IF NOT EXISTS water_debt_only BOOLEAN;
--   ALTER TABLE properties ADD COLUMN IF NOT EXISTS lien_amount NUMERIC;
--   ALTER TABLE properties ADD COLUMN IF NOT EXISTS lien_doc_type TEXT;
--   ALTER TABLE properties ADD COLUMN IF NOT EXISTS walk_score INTEGER;
--   ALTER TABLE properties ADD COLUMN IF NOT EXISTS transit_score INTEGER;
--   ALTER TABLE properties ADD COLUMN IF NOT EXISTS bike_score INTEGER;
--   ALTER TABLE properties ADD COLUMN IF NOT EXISTS tax_arrears NUMERIC;
--   ALTER TABLE properties ADD COLUMN IF NOT EXISTS annual_tax NUMERIC;
--   ALTER TABLE properties ADD COLUMN IF NOT EXISTS tax_bill_date DATE;
--   (api_quota table — run the full CREATE TABLE block above, or:)
--   CREATE TABLE IF NOT EXISTS api_quota (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), api_name TEXT NOT NULL, year_month TEXT NOT NULL, call_count INTEGER NOT NULL DEFAULT 0, monthly_limit INTEGER NOT NULL DEFAULT 50, updated_at TIMESTAMPTZ DEFAULT NOW(), UNIQUE (api_name, year_month));

-- Indexes for common filters
CREATE INDEX IF NOT EXISTS idx_properties_borough ON properties(borough);
CREATE INDEX IF NOT EXISTS idx_properties_deal_type ON properties(deal_type);
CREATE INDEX IF NOT EXISTS idx_properties_property_type ON properties(property_type);
CREATE INDEX IF NOT EXISTS idx_properties_price ON properties(price);
CREATE INDEX IF NOT EXISTS idx_properties_created_at ON properties(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_properties_bbl ON properties(bbl);
CREATE INDEX IF NOT EXISTS idx_deals_status ON deals(status);
CREATE INDEX IF NOT EXISTS idx_deals_property_id ON deals(property_id);

-- Violations table: HPD and DOB violations linked to properties
CREATE TABLE IF NOT EXISTS violations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    property_id UUID NOT NULL REFERENCES properties(id) ON DELETE CASCADE,
    source TEXT NOT NULL CHECK (source IN ('hpd', 'dob')),
    external_id TEXT,          -- source-specific violation ID for deduplication
    violation_type TEXT,
    description TEXT,
    status TEXT,
    issued_date DATE,
    closed_date DATE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (source, external_id)
);
CREATE INDEX IF NOT EXISTS idx_violations_property_id ON violations(property_id);

-- Sale history table: ACRIS deed records linked to properties
CREATE TABLE IF NOT EXISTS sale_history (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    property_id UUID NOT NULL REFERENCES properties(id) ON DELETE CASCADE,
    document_id TEXT NOT NULL,          -- ACRIS 16-char document ID
    doc_type TEXT,                       -- e.g. 'DEED', 'DEED, RC', 'CONDEED'
    sale_price NUMERIC,                  -- document_amt from ACRIS Master
    sale_date DATE,                      -- document_date from ACRIS Master
    recorded_at TIMESTAMPTZ,             -- recorded_datetime from ACRIS Master
    seller_name TEXT,                    -- party_type='1' from ACRIS Parties
    buyer_name TEXT,                     -- party_type='2' from ACRIS Parties
    percent_transferred NUMERIC,         -- percent_trans from ACRIS Master
    bbl TEXT,                            -- borough+block+lot used for lookup
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (property_id, document_id)    -- idempotent upserts
);
CREATE INDEX IF NOT EXISTS idx_sale_history_property_id ON sale_history(property_id);
CREATE INDEX IF NOT EXISTS idx_sale_history_sale_date ON sale_history(sale_date DESC);

-- Lien history table: prior DOF tax lien notices for properties (TES-40)
CREATE TABLE IF NOT EXISTS lien_history (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    property_id     UUID NOT NULL REFERENCES properties(id) ON DELETE CASCADE,
    bbl             TEXT NOT NULL,               -- 10-digit BBL
    tax_class       TEXT,                        -- '1', '2', '3', '4'
    building_class  TEXT,                        -- e.g. 'A1', 'D4'
    lien_cycle      TEXT,                        -- e.g. '90 Day Notice', 'In Rem'
    water_debt_only BOOLEAN DEFAULT FALSE,
    lien_amount     NUMERIC,
    notice_month    TEXT,                        -- YYYY-MM-DD from DOF 'month' field
    source_row_id   TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (property_id, source_row_id)
);
CREATE INDEX IF NOT EXISTS idx_lien_history_property_id  ON lien_history(property_id);
CREATE INDEX IF NOT EXISTS idx_lien_history_bbl          ON lien_history(bbl);
CREATE INDEX IF NOT EXISTS idx_lien_history_notice_month ON lien_history(notice_month DESC);

-- API quota table: tracks monthly call counts per external API to enforce limits
CREATE TABLE IF NOT EXISTS api_quota (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    api_name      TEXT NOT NULL,              -- e.g. 'rentcast', 'walkscore'
    year_month    TEXT NOT NULL,              -- e.g. '2026-02'
    call_count    INTEGER NOT NULL DEFAULT 0, -- calls made this month
    monthly_limit INTEGER NOT NULL DEFAULT 50,-- hard cap (raises error if exceeded)
    updated_at    TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (api_name, year_month)
);

-- HPD Building Registrations table: owner and managing agent contacts (TES-53)
-- Source: NYC HPD Multiple Dwelling Registrations (tesw-yqqr + feu5-w2e2)
-- Populated by data/ingest_hpd_registration.py
--
-- Migration (if table does not yet exist):
--   CREATE TABLE IF NOT EXISTS hpd_registrations (
--     id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
--     property_id UUID NOT NULL REFERENCES properties(id) ON DELETE CASCADE,
--     registration_id TEXT NOT NULL,
--     lifecycle_stage TEXT,
--     owner_name TEXT,
--     owner_type TEXT,
--     contact_name TEXT,
--     contact_type TEXT,
--     registration_end_date DATE,
--     created_at TIMESTAMPTZ DEFAULT NOW(),
--     UNIQUE (property_id, registration_id)
--   );
--   CREATE INDEX IF NOT EXISTS idx_hpd_registrations_property_id ON hpd_registrations(property_id);
CREATE TABLE IF NOT EXISTS hpd_registrations (
    id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    property_id           UUID NOT NULL REFERENCES properties(id) ON DELETE CASCADE,
    registration_id       TEXT NOT NULL,          -- HPD registrationid (stored as text)
    lifecycle_stage       TEXT,                   -- 'Active' or 'Terminated' (derived from registrationenddate)
    owner_name            TEXT,                   -- corporationname or firstname + lastname
    owner_type            TEXT,                   -- 'Individual', 'Corporation', 'Joint'
    contact_name          TEXT,                   -- managing agent / site manager name
    contact_type          TEXT,                   -- e.g. 'Agent', 'SiteManager', 'HeadOfficer'
    registration_end_date DATE,                   -- registrationenddate from tesw-yqqr
    created_at            TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (property_id, registration_id)         -- idempotent upserts
);
CREATE INDEX IF NOT EXISTS idx_hpd_registrations_property_id ON hpd_registrations(property_id);

-- Saved searches table: user-saved filter configurations for deal alerts
CREATE TABLE IF NOT EXISTS saved_searches (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    filters JSONB NOT NULL DEFAULT '{}',
    last_checked_at TIMESTAMPTZ DEFAULT NOW(),
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_saved_searches_created_at ON saved_searches(created_at DESC);

-- Seed a few sample properties for testing
INSERT INTO properties (address, borough, zip_code, property_type, deal_type, price, sqft, bedrooms, bathrooms, lat, lng, source, listed_at)
VALUES
    ('123 Atlantic Ave', 'Brooklyn', '11201', 'multifamily', 'foreclosure', 850000, 2400, 4, 2, 40.6892, -73.9943, 'manual', NOW()),
    ('456 Jamaica Ave', 'Queens', '11418', '1-4 family', 'tax_lien', 620000, 1800, 3, 2, 40.6962, -73.8089, 'manual', NOW()),
    ('789 Grand Concourse', 'Bronx', '10451', 'multifamily', 'foreclosure', 1100000, 4200, 8, 4, 40.8240, -73.9279, 'manual', NOW());
