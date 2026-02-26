"""NYC Open Data ingestion script — TES-7, TES-33, TES-38, TES-42.

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
  python data/ingest_nyc_open_data.py --lookback 24 # months of history (default 13)
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

import requests

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
REQUEST_TIMEOUT = 60  # seconds

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

# Deal type priority for cross-source merge (lower index = higher priority)
DEAL_TYPE_PRIORITY = ["foreclosure", "tax_lien", "listing", "off_market"]

# ACRIS borough code → single BBL digit (matches NYC's borough numbering)
ACRIS_BOROUGH_DIGIT: dict[str, str] = {
    "1": "1", "MN": "1",
    "2": "2", "BX": "2",
    "3": "3", "BK": "3",
    "4": "4", "QN": "4",
    "5": "5", "SI": "5",
}

# ---------------------------------------------------------------------------
# Address normalization
# ---------------------------------------------------------------------------

# Street suffix → USPS standard abbreviation
# Normalizing TO abbreviations so "123 Main Street" and "123 Main St" both
# become "123 Main St" and hash to the same dedup key.
_STREET_SUFFIX_MAP: dict[str, str] = {
    "ALLEE": "ALY", "ALLEY": "ALY", "ALLY": "ALY",
    "ANEX": "ANX", "ANNEX": "ANX",
    "ARCADE": "ARC",
    "AVENUE": "AVE", "AVEN": "AVE", "AVENU": "AVE", "AVN": "AVE", "AVNUE": "AVE",
    "BOULEVARD": "BLVD", "BOULV": "BLVD", "BOUL": "BLVD",
    "BRANCH": "BR",
    "BRIDGE": "BRG",
    "CIRCLE": "CIR", "CIRC": "CIR", "CIRCL": "CIR", "CRCL": "CIR", "CRCLE": "CIR",
    "COURT": "CT", "CRT": "CT",
    "COURTS": "CTS",
    "CROSSING": "XING",
    "DRIVE": "DR", "DRIV": "DR", "DRV": "DR",
    "EXPRESSWAY": "EXPY", "EXPRESS": "EXPY", "EXPWAY": "EXPY",
    "EXTENSION": "EXT", "EXTN": "EXT", "EXTNSN": "EXT",
    "FREEWAY": "FWY", "FREEWY": "FWY", "FRWAY": "FWY", "FRWY": "FWY",
    "HIGHWAY": "HWY", "HIGHWY": "HWY", "HIWAY": "HWY", "HIWY": "HWY", "HWAY": "HWY",
    "JUNCTION": "JCT", "JCTION": "JCT", "JCTN": "JCT",
    "LANE": "LN",
    "PARKWAY": "PKWY", "PARKWY": "PKWY", "PKWAY": "PKWY", "PWY": "PKWY",
    "PLACE": "PL",
    "PLAZA": "PLZ",
    "POINT": "PT",
    "ROAD": "RD", "ROADS": "RDS",
    "ROUTE": "RTE",
    "SQUARE": "SQ",
    "STATION": "STA",
    "STREET": "ST", "STRT": "ST", "STR": "ST",
    "TERRACE": "TER", "TERR": "TER",
    "TRAIL": "TRL", "TRAILS": "TRLS",
    "TURNPIKE": "TPKE", "TRNPK": "TPKE", "TURNPK": "TPKE",
    "VALLEY": "VLY",
    "VILLAGE": "VLG",
    "WALK": "WALK",
    "WAY": "WAY",
}

# Directional words → abbreviation
_DIRECTIONAL_MAP: dict[str, str] = {
    "NORTH": "N", "SOUTH": "S", "EAST": "E", "WEST": "W",
    "NORTHEAST": "NE", "NORTHWEST": "NW", "SOUTHEAST": "SE", "SOUTHWEST": "SW",
}

# Unit designator variants → canonical "APT"
_UNIT_PREFIX_RE = re.compile(
    r"\b(APARTMENT|SUITE|STE|UNIT|NO\.?|NUM\.?|#)\s*",
    re.IGNORECASE,
)


def normalize_address(raw: str) -> str:
    """Return a canonical address string for deduplication and storage.

    Steps:
    1. Collapse whitespace; uppercase for processing
    2. Normalize unit designators (Apartment/Suite/Unit/# → Apt)
    3. Abbreviate street suffixes (Street → St, Avenue → Ave, etc.)
    4. Abbreviate directionals (North → N, etc.)
    5. Title-case the result for display
    """
    if not raw:
        return raw

    addr = " ".join(raw.strip().split()).upper()

    # Normalize unit designators to "APT"
    addr = _UNIT_PREFIX_RE.sub("APT ", addr)
    # Collapse any double spaces introduced above
    addr = " ".join(addr.split())

    tokens = addr.split()
    normalized = []
    for tok in tokens:
        clean = tok.rstrip(".,")
        mapped = _STREET_SUFFIX_MAP.get(clean) or _DIRECTIONAL_MAP.get(clean)
        normalized.append(mapped if mapped else clean)

    return " ".join(normalized).title()

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


def soda_get(dataset_id: str, params: dict, _retries: int = 3) -> list[dict]:
    """Fetch one page of results from a Socrata dataset, retrying on transient errors."""
    import logging as _log
    _logger = _log.getLogger(__name__)
    url = f"{SODA_BASE}/{dataset_id}.json"
    for attempt in range(1, _retries + 1):
        try:
            resp = requests.get(url, params=params, headers=_soda_headers(), timeout=REQUEST_TIMEOUT)
            # Retry on 5xx server errors (transient); raise immediately on 4xx client errors
            if resp.status_code >= 500:
                if attempt == _retries:
                    resp.raise_for_status()
                wait = 2 ** attempt  # 2s, 4s
                _logger.warning(
                    "Socrata %d server error (attempt %d/%d), retrying in %ds",
                    resp.status_code, attempt, _retries, wait,
                )
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp.json()
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as exc:
            if attempt == _retries:
                raise
            wait = 2 ** attempt  # 2s, 4s
            _logger.warning(
                "Socrata request failed (attempt %d/%d), retrying in %ds: %s",
                attempt, _retries, wait, exc,
            )
            time.sleep(wait)
    return []  # unreachable


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
# BBL helpers
# ---------------------------------------------------------------------------

def construct_bbl(raw_borough: str, block: str, lot: str) -> Optional[str]:
    """Build a 10-digit BBL string from ACRIS borough code, block, and lot.

    BBL format: B(1) + BLOCK(5, zero-padded) + LOT(4, zero-padded)
    Example: borough=3, block=229, lot=1 → '3002290001'
    Returns None if any component is missing or non-numeric.
    """
    digit = ACRIS_BOROUGH_DIGIT.get((raw_borough or "").strip().upper())
    block_s = (block or "").strip()
    lot_s = (lot or "").strip()
    if not digit or not block_s.isdigit() or not lot_s.isdigit():
        return None
    return f"{digit}{int(block_s):05d}{int(lot_s):04d}"


def extract_bbl_from_feature(feature: dict) -> Optional[str]:
    """Extract BBL from a NYC GeoSearch API feature dict, or None.

    GeoSearch (Pelias) returns BBL in the addendum.pad.bbl field.
    """
    props = feature.get("properties", {})
    addendum = props.get("addendum") or {}
    pad = addendum.get("pad") or {}
    bbl = pad.get("bbl")
    if bbl:
        return str(bbl).strip()
    return None


# ---------------------------------------------------------------------------
# Geocoding — NYC Planning GeoSearch API
# ---------------------------------------------------------------------------

GEOSEARCH_URL = "https://geosearch.planninglabs.nyc/v2/search"
_geocache: dict[str, tuple[Optional[float], Optional[float], Optional[str]]] = {}


def geosearch_feature(address: str, borough: str) -> Optional[dict]:
    """Call the NYC GeoSearch API and return the top feature dict, or None.

    Retries up to 3 times with exponential backoff on failure.
    This is the shared primitive used by both geocode_address() and
    the BBL backfill script (data/backfill_bbl.py).
    """
    text = f"{address}, {borough}"
    for attempt in range(3):
        try:
            resp = requests.get(
                GEOSEARCH_URL,
                params={"text": text, "size": 1},
                timeout=10,
            )
            resp.raise_for_status()
            features = resp.json().get("features", [])
            return features[0] if features else None
        except Exception as exc:
            if attempt < 2:
                time.sleep(2 ** attempt)
            else:
                log.warning("GeoSearch failed for %r: %s", text, exc)
    return None


def geocode_address(address: str, borough: str) -> tuple[Optional[float], Optional[float], Optional[str]]:
    """Return (lat, lng, bbl) for a NYC address using the NYC GeoSearch API.

    BBL is the 10-digit Borough-Block-Lot parcel identifier returned by the
    GeoSearch API at features[0].properties.addendum.pad.bbl. It is needed
    for ACRIS deed sale lookups (TES-42/TES-44).

    Free, no API key required, optimised for NYC addresses.
    Returns (None, None, None) on any failure without raising.
    """
    cache_key = f"{address}|{borough}"
    if cache_key in _geocache:
        return _geocache[cache_key]

    feature = geosearch_feature(address, borough)
    if feature:
        lng, lat = feature["geometry"]["coordinates"]
        bbl = feature["properties"].get("addendum", {}).get("pad", {}).get("bbl")
        result = (float(lat), float(lng), bbl or None)
    else:
        result = (None, None, None)

    _geocache[cache_key] = result
    return result


def normalize_borough(raw: str) -> Optional[str]:
    """Normalize a raw borough string/code to our schema value."""
    return BOROUGH_MAP.get(raw.strip().upper())


# ---------------------------------------------------------------------------
# Rolling date window helper
# ---------------------------------------------------------------------------

def _lookback_date(months: int) -> str:
    """Return an ISO datetime string `months` months in the past."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=months * 30)
    return cutoff.strftime("%Y-%m-%dT00:00:00.000")


# ---------------------------------------------------------------------------
# Foreclosure ingestion (ACRIS)
# ---------------------------------------------------------------------------

def fetch_foreclosures(limit: Optional[int] = None, lookback_months: int = 13) -> list[dict]:
    """Fetch foreclosure judgment records from ACRIS.

    Uses a rolling lookback window instead of a hardcoded cutoff date so
    daily runs stay current without needing code changes.
    """
    since = _lookback_date(lookback_months)
    log.info("Fetching foreclosure JUDG records since %s...", since)

    master_rows = soda_get_all(
        ACRIS_MASTER_ID,
        where=f"doc_type='JUDG' AND recorded_datetime > '{since}'",
        select="document_id,doc_type,document_date,document_amt,recorded_datetime",
        limit=limit,
    )
    log.info("  got %d master JUDG records", len(master_rows))
    if not master_rows:
        return []

    master_by_id: dict[str, dict] = {r["document_id"]: r for r in master_rows}
    doc_ids = list(master_by_id.keys())

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

    properties: list[dict] = []
    seen: set[str] = set()

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

        raw_address = f"{street_num} {street_name}"
        unit = (legal.get("unit") or "").strip()
        if unit:
            raw_address += f" Apt {unit}"

        address = normalize_address(raw_address)
        dedup_key = f"{address}|{borough}"
        if dedup_key in seen:
            continue
        seen.add(dedup_key)

        price_raw = master.get("document_amt")
        price = float(price_raw) if price_raw else None
        listed_at = master.get("document_date") or master.get("recorded_datetime")

        # Construct BBL from ACRIS block/lot — geocoding pass may refine this
        bbl = construct_bbl(raw_borough, legal.get("block", ""), legal.get("lot", ""))

        properties.append({
            "address": address,
            "borough": borough,
            "deal_type": "foreclosure",
            "price": price,
            "source": "nyc_open_data",
            "source_url": f"https://data.cityofnewyork.us/resource/{ACRIS_MASTER_ID}.json",
            "listed_at": listed_at,
            "bbl": bbl,
            "_needs_geocode": True,
        })

    log.info("  mapped %d unique foreclosure properties", len(properties))
    return properties


# ---------------------------------------------------------------------------
# Tax lien ingestion (DOF Tax Lien Sale List)
# ---------------------------------------------------------------------------

def fetch_tax_liens(limit: Optional[int] = None, lookback_months: int = 13) -> list[dict]:
    """Fetch tax lien sale properties from the DOF Tax Lien Sale Lists dataset.

    Filters by a rolling date window on the `month` field so daily runs
    only re-fetch recent records instead of the entire historical dataset.
    """
    since = _lookback_date(lookback_months)
    log.info("Fetching tax lien records since %s...", since)

    rows = soda_get_all(
        TAX_LIEN_DATASET_ID,
        where=f"month > '{since}'",
        select="borough,block,lot,house_number,street_name,zip_code,month,"
               "building_class,tax_class_code,cycle,water_debt_only",
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
            raw_address = f"Block {block} Lot {lot}"
        else:
            raw_address = f"{house_num} {street}"

        address = normalize_address(raw_address)
        zip_code = (row.get("zip_code") or "").strip()

        dedup_key = f"{address}|{borough}"
        if dedup_key in seen:
            continue
        seen.add(dedup_key)

        # Construct BBL from DOF block/lot — geocoding pass may refine this
        raw_block = (row.get("block") or "").strip()
        raw_lot = (row.get("lot") or "").strip()
        bbl = construct_bbl(raw_borough, raw_block, raw_lot)

        # water_debt_only comes as "YES"/"NO" string from Socrata
        water_raw = (row.get("water_debt_only") or "").strip().upper()
        water_debt_only = True if water_raw == "YES" else (False if water_raw == "NO" else None)

        properties.append({
            "address": address,
            "borough": borough,
            "zip_code": zip_code or None,
            "deal_type": "tax_lien",
            "source": "nyc_open_data",
            "source_url": f"https://data.cityofnewyork.us/resource/{TAX_LIEN_DATASET_ID}.json",
            "listed_at": row.get("month"),
            "bbl": bbl,
            "building_class": row.get("building_class") or None,
            "block": raw_block or None,
            "lot": raw_lot or None,
            "tax_class_code": row.get("tax_class_code") or None,
            "lien_cycle": row.get("cycle") or None,
            "water_debt_only": water_debt_only,
            "_needs_geocode": True,
        })

    log.info("  mapped %d unique tax lien properties", len(properties))
    return properties


# ---------------------------------------------------------------------------
# Cross-source rollup
# ---------------------------------------------------------------------------

def merge_properties(properties: list[dict]) -> list[dict]:
    """Merge records from multiple sources that share the same address+borough.

    When the same normalized address appears in more than one source:
    - deal_type: highest priority wins (foreclosure > tax_lien > listing > off_market)
    - price: first non-None value across sources
    - listed_at: most recent non-None value
    - zip_code, other fields: first non-None value
    """
    buckets: dict[str, list[dict]] = {}
    for p in properties:
        key = f"{p['address']}|{p['borough']}"
        buckets.setdefault(key, []).append(p)

    merged: list[dict] = []
    cross_source_count = 0

    for key, group in buckets.items():
        if len(group) == 1:
            merged.append(group[0])
            continue

        cross_source_count += 1
        source_types = [p["deal_type"] for p in group]
        log.info("  cross-source merge: %s (%s)", key, " + ".join(source_types))

        # Highest-priority deal_type
        best_deal_type = min(
            source_types,
            key=lambda dt: DEAL_TYPE_PRIORITY.index(dt) if dt in DEAL_TYPE_PRIORITY else 99,
        )

        base = dict(group[0])
        base["deal_type"] = best_deal_type

        for p in group[1:]:
            if base.get("price") is None:
                base["price"] = p.get("price")
            if base.get("zip_code") is None:
                base["zip_code"] = p.get("zip_code")
            # Keep most recent listed_at
            if p.get("listed_at") and base.get("listed_at"):
                if p["listed_at"] > base["listed_at"]:
                    base["listed_at"] = p["listed_at"]
            elif p.get("listed_at"):
                base["listed_at"] = p["listed_at"]

        merged.append(base)

    if cross_source_count:
        log.info(
            "Cross-source merges: %d (reduced %d → %d records)",
            cross_source_count, len(properties), len(merged),
        )

    return merged


# ---------------------------------------------------------------------------
# Geocoding pass
# ---------------------------------------------------------------------------

def add_coordinates(properties: list[dict]) -> None:
    """Geocode any properties that have _needs_geocode=True, storing lat/lng/bbl."""
    needs_geocode = [p for p in properties if p.pop("_needs_geocode", False)]
    log.info("Geocoding %d properties (this may take a moment)...", len(needs_geocode))

    for i, prop in enumerate(needs_geocode):
        lat, lng, bbl = geocode_address(prop["address"], prop["borough"])
        prop["lat"] = lat
        prop["lng"] = lng
        prop["bbl"] = bbl
        if (i + 1) % 10 == 0:
            log.info("  geocoded %d/%d", i + 1, len(needs_geocode))


# ---------------------------------------------------------------------------
# Supabase upsert
# ---------------------------------------------------------------------------

UPSERT_COLUMNS = [
    "address", "borough", "zip_code", "deal_type", "price",
    "source", "source_url", "lat", "lng", "bbl", "listed_at",
    "building_class", "block", "lot", "tax_class_code", "lien_cycle", "water_debt_only",
]


def upsert_properties(properties: list[dict], dry_run: bool = False) -> int:
    """Upsert a list of property dicts into Supabase. Returns upsert count."""
    if not properties:
        log.info("No properties to upsert.")
        return 0

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
    parser.add_argument(
        "--lookback",
        type=int,
        default=13,
        metavar="MONTHS",
        help="Months of history to fetch per source (default: 13)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    all_properties: list[dict] = []

    if args.source in ("foreclosure", "all"):
        all_properties.extend(
            fetch_foreclosures(limit=args.limit, lookback_months=args.lookback)
        )

    if args.source in ("tax_lien", "all"):
        all_properties.extend(
            fetch_tax_liens(limit=args.limit, lookback_months=args.lookback)
        )

    if not all_properties:
        log.info("No properties fetched. Exiting.")
        return

    log.info("Total properties fetched before merge: %d", len(all_properties))

    all_properties = merge_properties(all_properties)
    log.info("Total properties after merge: %d", len(all_properties))

    if not args.no_geocode:
        add_coordinates(all_properties)

    upsert_properties(all_properties, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
