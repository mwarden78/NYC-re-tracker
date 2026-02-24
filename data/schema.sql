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
    updated_at TIMESTAMPTZ DEFAULT NOW()
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

-- Indexes for common filters
CREATE INDEX IF NOT EXISTS idx_properties_borough ON properties(borough);
CREATE INDEX IF NOT EXISTS idx_properties_deal_type ON properties(deal_type);
CREATE INDEX IF NOT EXISTS idx_properties_property_type ON properties(property_type);
CREATE INDEX IF NOT EXISTS idx_properties_price ON properties(price);
CREATE INDEX IF NOT EXISTS idx_properties_created_at ON properties(created_at DESC);
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
