"""NYC Open Data ingestion script — TES-7.

Fetches foreclosure judgment and tax lien data from NYC Open Data
(Socrata SODA API) and upserts into the Supabase `properties` table.

Data sources:
  Foreclosures — ACRIS Real Property Master (doc_type=JUDG)
    Master:  https://data.cityofnewyork.us/resource/bnx9-e6tj.json
    Legals:  https://data.cityofnewyork.us/resource/8h5j-fqxa.json  (addresses)
  Tax Liens — DOF Tax Lien Sale Lists
    https://data.cityofnewyork.us/resource/9rz4-mjek.json

No API key is required for read-only Socrata access (up to 1,000 rows/page).
Set NYC_OPEN_DATA_APP_TOKEN in .env for higher rate limits.

Usage:
  python data/ingest_nyc_open_data.py               # ingest all sources
  python data/ingest_nyc_open_data.py --dry-run     # preview without writing
  python data/ingest_nyc_open_data.py --source foreclosure
  python data/ingest_nyc_open_data.py --source tax_lien
  python data/ingest_nyc_open_data.py --limit 50    # cap records per source
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from typing import Optional

import requests
from geopy.exc import GeocoderTimedOut
from geopy.geocoders import Nominatim

# Allow running from project root: python data/ingest_nyc_open_data.py
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from utils.supabase_client import get_client  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SODA_BASE = "https://data.cityofnewyork.us/resource"
PAGE_SIZE = 1000
REQUEST_TIMEOUT = 30  # seconds

# ACRIS dataset IDs
ACRIS_MASTER_ID = "bnx9-e6tj"   # Real Property Master (doc_type, dates, amounts)
ACRIS_LEGALS_ID = "8h5j-fqxa"  # Real Property Legals (block/lot/address)

# DOF Tax Lien Sale Lists dataset ID (updated — old 9rrd-3h26 is retired)
TAX_LIEN_DATASET_ID = "9rz4-mjek"

BOROUGH_MAP: dict[str, str] = {
    "1": "Manhattan",
    "2": "Bronx",
    "3": "Brooklyn",
    "4": "Queens",
    "5": "Staten Island",
    "MN": "Manhattan",
    "BX": "Bronx",
    "BK": "Brooklyn",
    "QN": "Queens",
    "SI": "Staten Island",
    "MANHATTAN": "Manhattan",
    "BRONX": "Bronx",
    "BROOKLYN": "Brooklyn",
    "QUEENS": "Queens",
    "STATEN ISLAND": "Staten Island",
}


# ---------------------------------------------------------------------------
# Socrata HTTP helpers
# ---------------------------------------------------------------------------

def _soda_headers() -> dict[str, str]:
    """Return headers for Socrata SODA requests, including optional app token."""
    headers = {"Accept": "application/json"}
    token = os.environ.get("NYC_OPEN_DATA_APP_TOKEN")
    if token:
        headers["X-App-Token"] = token
    return headers


def soda_get(dataset_id: str, params: dict) -> list[dict]:
    """Fetch one page of results from a Socrata dataset."""
    url = f"{SODA_BASE}/{dataset_id}.json"
    resp = requests.get(url, params=params, headers=_soda_headers(), timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def soda_get_all(dataset_id: str, where: str, select: str = "*", limit: Optional[int] = None) -> list[dict]:
    """Fetch all records matching `where` from a Socrata dataset (auto-paginated)."""
    results: list[dict] = []
    offset = 0
    page_size = PAGE_SIZE if limit is None else min(PAGE_SIZE, limit)

    while True:
        rows = soda_get(dataset_id, {
            "$where": where,
            "$select": select,
            "$limit": page_size,
            "$offset": offset,
            "$order": ":id",
        })
        results.extend(rows)
        log.debug("  fetched %d rows (offset %d)", len(rows), offset)
        if len(rows) < page_size:
            break
        offset += page_size
        if limit is not None and len(results) >= limit:
            results = results[:limit]
            break
        time.sleep(0.1)  # gentle rate limiting

    return results


# ---------------------------------------------------------------------------
# Geocoding
# ---------------------------------------------------------------------------

_geolocator = Nominatim(user_agent="nyc-re-tracker/1.0")
_geocache: dict[str, tuple[Optional[float], Optional[float]]] = {}


def geocode_address(address: str, borough: str) -> tuple[Optional[float], Optional[float]]:
    """Return (lat, lng) for a NYC address, or (None, None) on failure."""
    query = f"{address}, {borough}, New York City, NY"
    if query in _geocache:
        return _geocache[query]

    for attempt in range(3):
        try:
            time.sleep(1.0)  # Nominatim rate limit: 1 req/sec
            loc = _geolocator.geocode(query, timeout=10)
            if loc:
                result = (loc.latitude, loc.longitude)
                _geocache[query] = result
                return result
            _geocache[query] = (None, None)
            return (None, None)
        except GeocoderTimedOut:
            if attempt < 2:
                time.sleep(2 ** attempt)
            else:
                log.warning("Geocoder timed out for: %s", query)
    _geocache[query] = (None, None)
    return (None, None)


def normalize_borough(raw: str) -> Optional[str]:
    """Normalize a raw borough string/code to our schema value."""
    return BOROUGH_MAP.get(raw.strip().upper())


# ---------------------------------------------------------------------------
# Foreclosure ingestion (ACRIS Lis Pendens)
# ---------------------------------------------------------------------------

def fetch_foreclosures(limit: Optional[int] = None) -> list[dict]:
    """
    Fetch foreclosure judgment records from ACRIS.

    Uses doc_type=JUDG (court judgments recorded against properties),
    which includes foreclosure judgments filed with the NYC Register.
    Note: lis pendens are filed with County Clerks, not ACRIS, so JUDG
    is the closest available foreclosure signal in this dataset.

    Step 1: Pull recent JUDG documents from the Master table.
    Step 2: Look up the property address in the Legals table.
    """
    log.info("Fetching foreclosure judgment records from ACRIS master...")

    # Fetch master records where doc_type = 'JUDG', last 12 months
    master_rows = soda_get_all(
        ACRIS_MASTER_ID,
        where="doc_type='JUDG' AND recorded_datetime > '2024-01-01T00:00:00.000'",
        select="document_id,doc_type,document_date,document_amt,recorded_datetime",
        limit=limit,
    )
    log.info("  got %d master JUDG records", len(master_rows))
    if not master_rows:
        return []

    # Build a lookup by document_id for quick merging
    master_by_id: dict[str, dict] = {r["document_id"]: r for r in master_rows}
    doc_ids = list(master_by_id.keys())

    # Fetch legals for those document IDs (batch in chunks to stay within URL length)
    legals: list[dict] = []
    chunk_size = 50
    for i in range(0, len(doc_ids), chunk_size):
        chunk = doc_ids[i : i + chunk_size]
        ids_clause = ", ".join(f"'{d}'" for d in chunk)
        chunk_legals = soda_get_all(
            ACRIS_LEGALS_ID,
            where=f"document_id in ({ids_clause})",
            select="document_id,borough,block,lot,street_number,street_name,unit",
        )
        legals.extend(chunk_legals)
        time.sleep(0.2)

    log.info("  got %d legal records", len(legals))

    # Merge and map to our properties schema
    properties: list[dict] = []
    seen_addresses: set[str] = set()

    for legal in legals:
        doc_id = legal.get("document_id")
        master = master_by_id.get(doc_id, {})

        raw_borough = legal.get("borough", "")
        borough = normalize_borough(raw_borough)
        if not borough:
            continue

        street_num = (legal.get("street_number") or "").strip()
        street_name = (legal.get("street_name") or "").strip()
        if not street_num or not street_name:
            continue

        address = f"{street_num} {street_name}".title()
        unit = (legal.get("unit") or "").strip()
        if unit:
            address += f" #{unit}"

        dedup_key = f"{address}|{borough}"
        if dedup_key in seen_addresses:
            continue
        seen_addresses.add(dedup_key)

        price_raw = master.get("document_amt")
        price = float(price_raw) if price_raw else None

        listed_at = master.get("document_date") or master.get("recorded_datetime")

        properties.append({
            "address": address,
            "borough": borough,
            "deal_type": "foreclosure",
            "price": price,
            "source": "nyc_open_data",
            "source_url": f"https://data.cityofnewyork.us/resource/{ACRIS_MASTER_ID}.json",
            "listed_at": listed_at,
            "_needs_geocode": True,
        })

    log.info("  mapped %d unique foreclosure properties", len(properties))
    return properties


# ---------------------------------------------------------------------------
# Tax lien ingestion (DOF Tax Lien Sale List)
# ---------------------------------------------------------------------------

def fetch_tax_liens(limit: Optional[int] = None) -> list[dict]:
    """
    Fetch tax lien sale properties from the DOF Tax Lien Sale Lists dataset.

    Dataset 9rz4-mjek fields: house_number, street_name, borough (1-5),
    zip_code, block, lot, month (date), cycle, building_class.
    """
    log.info("Fetching tax lien sale records from DOF...")

    rows = soda_get_all(
        TAX_LIEN_DATASET_ID,
        where="month IS NOT NULL",
        select="borough,block,lot,house_number,street_name,zip_code,month,building_class",
        limit=limit,
    )
    log.info("  got %d tax lien records", len(rows))

    properties: list[dict] = []
    seen: set[str] = set()

    for row in rows:
        raw_borough = row.get("borough", "")
        borough = normalize_borough(raw_borough)
        if not borough:
            continue

        house_num = (row.get("house_number") or "").strip()
        street = (row.get("street_name") or "").strip()
        if not house_num or not street:
            block = row.get("block", "")
            lot = row.get("lot", "")
            address = f"Block {block} Lot {lot}".title()
        else:
            address = f"{house_num} {street}".title()

        zip_code = (row.get("zip_code") or "").strip()

        dedup_key = f"{address}|{borough}"
        if dedup_key in seen:
            continue
        seen.add(dedup_key)

        properties.append({
            "address": address,
            "borough": borough,
            "zip_code": zip_code or None,
            "deal_type": "tax_lien",
            "source": "nyc_open_data",
            "source_url": f"https://data.cityofnewyork.us/resource/{TAX_LIEN_DATASET_ID}.json",
            "listed_at": row.get("month"),
            "_needs_geocode": True,
        })

    log.info("  mapped %d unique tax lien properties", len(properties))
    return properties


# ---------------------------------------------------------------------------
# Geocoding pass
# ---------------------------------------------------------------------------

def add_coordinates(properties: list[dict]) -> None:
    """Geocode any properties that have _needs_geocode=True."""
    needs_geocode = [p for p in properties if p.pop("_needs_geocode", False)]
    log.info("Geocoding %d properties (this may take a moment)...", len(needs_geocode))

    for i, prop in enumerate(needs_geocode):
        lat, lng = geocode_address(prop["address"], prop["borough"])
        prop["lat"] = lat
        prop["lng"] = lng
        if (i + 1) % 10 == 0:
            log.info("  geocoded %d/%d", i + 1, len(needs_geocode))


# ---------------------------------------------------------------------------
# Supabase upsert
# ---------------------------------------------------------------------------

UPSERT_COLUMNS = [
    "address", "borough", "zip_code", "deal_type", "price",
    "source", "source_url", "lat", "lng", "listed_at",
]


def upsert_properties(properties: list[dict], dry_run: bool = False) -> int:
    """Upsert a list of property dicts into Supabase. Returns upsert count."""
    if not properties:
        log.info("No properties to upsert.")
        return 0

    # Strip internal-only keys and keep only schema columns
    rows = [{col: p.get(col) for col in UPSERT_COLUMNS} for p in properties]

    if dry_run:
        log.info("[DRY RUN] Would upsert %d properties:", len(rows))
        for r in rows[:5]:
            log.info("  %s", r)
        if len(rows) > 5:
            log.info("  ... and %d more", len(rows) - 5)
        return len(rows)

    client = get_client()
    batch_size = 100
    total = 0
    num_batches = -(-len(rows) // batch_size)  # ceiling division

    for i in range(0, len(rows), batch_size):
        batch = rows[i : i + batch_size]
        client.table("properties").upsert(batch, on_conflict="address,borough").execute()
        total += len(batch)
        log.info("  upserted batch %d/%d (%d rows)", i // batch_size + 1, num_batches, len(batch))

    log.info("Upsert complete. Total: %d properties.", total)
    return total


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Ingest NYC Open Data into the Supabase properties table.",
    )
    parser.add_argument(
        "--source",
        choices=["foreclosure", "tax_lien", "all"],
        default="all",
        help="Which data source to ingest (default: all)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        metavar="N",
        help="Cap records fetched per source (default: no limit)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch and transform but do not write to Supabase",
    )
    parser.add_argument(
        "--no-geocode",
        action="store_true",
        help="Skip geocoding (faster, lat/lng will be null)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    all_properties: list[dict] = []

    if args.source in ("foreclosure", "all"):
        all_properties.extend(fetch_foreclosures(limit=args.limit))

    if args.source in ("tax_lien", "all"):
        all_properties.extend(fetch_tax_liens(limit=args.limit))

    if not all_properties:
        log.info("No properties fetched. Exiting.")
        return

    log.info("Total properties fetched: %d", len(all_properties))

    if not args.no_geocode:
        add_coordinates(all_properties)

    upsert_properties(all_properties, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
