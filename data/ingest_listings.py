"""RentCast for-sale listings ingestion script — TES-74.

Paginates through active NYC for-sale listings from the RentCast API and
upserts them into the `listings` Supabase table.  After collecting all
listings that have a BBL (from NYC GeoSearch geocoding), fetches PLUTO parcel
attributes in batches from Socrata and writes them back.

RentCast endpoint: GET /v1/listings/sale
  city=New York, state=NY, status=active, limit=500, offset=N

PLUTO dataset (NYC DCP, Socrata 64uk-42ks):
  Additional fields vs enrich_pluto.py: bldgclass, residfar, builtfar, unitstotal

Usage:
  python data/ingest_listings.py                     # full NYC sweep
  python data/ingest_listings.py --limit 20          # fetch 20 listings (1 API call)
  python data/ingest_listings.py --dry-run           # print rows, no DB writes
  python data/ingest_listings.py --borough BK        # Brooklyn only (filter post-fetch)
  python data/ingest_listings.py --skip-geocode      # skip BBL/PLUTO enrichment (faster)

Quota: each paginated RentCast request costs 1 call against the 50/month cap.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from datetime import date
from typing import Optional

import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from utils.supabase_client import get_client          # noqa: E402
from utils.quota import check_and_increment, log_usage_summary, QuotaExceededError  # noqa: E402
from data.ingest_nyc_open_data import geocode_address  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

RENTCAST_BASE = "https://api.rentcast.io/v1/listings/sale"
RENTCAST_PAGE_SIZE = 500
RENTCAST_MONTHLY_LIMIT = 50

# City names to sweep by default — excludes "New York" (Manhattan) which is
# already ingested. Queens addresses sometimes use neighbourhood names
# (Flushing, Jamaica, Astoria…) so city="Queens" won't be exhaustive, but
# captures the bulk of the inventory.
DEFAULT_CITY_SWEEPS = ["Brooklyn", "Bronx", "Staten Island", "Queens"]

# ---------------------------------------------------------------------------
# Fetch cache — saves raw API results to disk so a failed upsert can be
# retried without burning additional RentCast quota calls.
# ---------------------------------------------------------------------------
_CACHE_PATH = os.path.join(os.path.dirname(__file__), ".rentcast_cache.json")


def _load_cache(cities: list[str]) -> list[dict] | None:
    """Return cached listings if the cache is from today and covers the same cities."""
    try:
        with open(_CACHE_PATH) as f:
            data = json.load(f)
        if data.get("date") != str(date.today()):
            return None
        if sorted(data.get("cities", [])) != sorted(cities):
            return None
        listings = data.get("listings", [])
        log.info("Loaded %d listings from cache (%s) — skipping RentCast fetch.", len(listings), _CACHE_PATH)
        return listings
    except FileNotFoundError:
        return None
    except Exception as exc:
        log.warning("Could not read fetch cache (%s): %s — fetching fresh.", _CACHE_PATH, exc)
        return None


def _save_cache(cities: list[str], listings: list[dict]) -> None:
    """Persist raw listings to disk so retries can skip the API fetch."""
    try:
        with open(_CACHE_PATH, "w") as f:
            json.dump({"date": str(date.today()), "cities": cities, "listings": listings}, f)
        log.info("Saved %d listings to fetch cache (%s).", len(listings), _CACHE_PATH)
    except Exception as exc:
        log.warning("Could not write fetch cache: %s", exc)


def _clear_cache() -> None:
    """Delete the cache file after a successful upsert."""
    try:
        os.remove(_CACHE_PATH)
        log.info("Fetch cache cleared.")
    except FileNotFoundError:
        pass
    except Exception as exc:
        log.warning("Could not clear fetch cache: %s", exc)

PLUTO_DATASET_ID = "64uk-42ks"
PLUTO_SODA_BASE = "https://data.cityofnewyork.us/resource"
PLUTO_BATCH_SIZE = 100
REQUEST_TIMEOUT = 30

# Zip → borough mapping for the five NYC boroughs.
# Ranges are approximate; the lookup covers all USPS zips assigned to NYC.
_ZIP_BOROUGH: dict[str, str] = {}

def _build_zip_borough_map() -> dict[str, str]:
    mapping: dict[str, str] = {}
    # Manhattan: 10001–10282
    for z in range(10001, 10283):
        mapping[str(z)] = "Manhattan"
    # Bronx: 10451–10475
    for z in range(10451, 10476):
        mapping[str(z)] = "Bronx"
    # Brooklyn: 11201–11256
    for z in range(11201, 11257):
        mapping[str(z)] = "Brooklyn"
    # Queens: 11004–11005, 11101–11106, 11354–11697
    for z in [11004, 11005]:
        mapping[str(z)] = "Queens"
    for z in range(11101, 11107):
        mapping[str(z)] = "Queens"
    for z in range(11354, 11698):
        mapping[str(z)] = "Queens"
    # Staten Island: 10301–10314
    for z in range(10301, 10315):
        mapping[str(z)] = "Staten Island"
    return mapping

ZIP_BOROUGH = _build_zip_borough_map()

BOROUGH_ABBREV: dict[str, str] = {
    "MN": "Manhattan",
    "BK": "Brooklyn",
    "BX": "Bronx",
    "QN": "Queens",
    "SI": "Staten Island",
    "MANHATTAN": "Manhattan",
    "BROOKLYN": "Brooklyn",
    "BRONX": "Bronx",
    "QUEENS": "Queens",
    "STATEN ISLAND": "Staten Island",
}


# ---------------------------------------------------------------------------
# RentCast helpers
# ---------------------------------------------------------------------------

def _rentcast_headers() -> dict[str, str]:
    api_key = os.environ.get("RENTCAST_API_KEY")
    if not api_key:
        raise RuntimeError("RENTCAST_API_KEY not set in environment")
    return {"X-Api-Key": api_key, "Accept": "application/json"}


def fetch_rentcast_page(offset: int, limit: int = RENTCAST_PAGE_SIZE, city: str = "New York") -> list[dict]:
    """Fetch one page of active NYC for-sale listings from RentCast."""
    params = {
        "city": city,
        "state": "NY",
        "status": "active",
        "limit": limit,
        "offset": offset,
    }
    resp = requests.get(
        RENTCAST_BASE,
        headers=_rentcast_headers(),
        params=params,
        timeout=REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    data = resp.json()
    # RentCast returns a list directly for this endpoint
    if isinstance(data, list):
        return data
    # Some versions wrap in {"listings": [...]}
    if isinstance(data, dict):
        return data.get("listings", data.get("data", []))
    return []


# ---------------------------------------------------------------------------
# Normalization helpers
# ---------------------------------------------------------------------------

def _safe_float(val) -> Optional[float]:
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


_PG_INT_MAX = 2_147_483_647  # PostgreSQL INTEGER upper bound

def _safe_int(val) -> Optional[int]:
    f = _safe_float(val)
    if f is None:
        return None
    i = int(round(f))
    return i if abs(i) <= _PG_INT_MAX else None  # drop values that overflow PG INTEGER


def borough_from_zip(zip_code: Optional[str]) -> Optional[str]:
    if not zip_code:
        return None
    return ZIP_BOROUGH.get(str(zip_code).strip()[:5])


def normalize_listing(raw: dict) -> dict:
    """Map a raw RentCast listing dict to our listings table schema."""
    zip_code = str(raw.get("zipCode") or raw.get("zip_code") or "").strip() or None
    borough = borough_from_zip(zip_code)

    price = _safe_int(raw.get("price"))
    sqft = _safe_int(raw.get("squareFootage") or raw.get("sqft"))
    price_per_sqft: Optional[float] = None
    if price and sqft and sqft > 0:
        price_per_sqft = round(price / sqft, 2)

    # RentCast may return coordinates at top level or nested
    lat = _safe_float(raw.get("latitude") or raw.get("lat"))
    lng = _safe_float(raw.get("longitude") or raw.get("lng"))

    return {
        "rentcast_id":    str(raw.get("id") or raw.get("listingId") or ""),
        "address":        str(raw.get("formattedAddress") or raw.get("address") or "").strip(),
        "borough":        borough,
        "zip_code":       zip_code,
        "latitude":       lat,
        "longitude":      lng,
        "price":          price,
        "price_per_sqft": price_per_sqft,
        "sqft":           sqft,
        "lot_sqft":       _safe_int(raw.get("lotSize") or raw.get("lot_sqft")),
        "beds":           _safe_float(raw.get("bedrooms") or raw.get("beds")),
        "baths":          _safe_float(raw.get("bathrooms") or raw.get("baths")),
        "property_type":  str(raw.get("propertyType") or raw.get("property_type") or "").strip() or None,
        "year_built":     _safe_int(raw.get("yearBuilt") or raw.get("year_built")),
        "status":         str(raw.get("status") or "active").lower(),
        "days_on_market": _safe_int(raw.get("daysOnMarket") or raw.get("days_on_market")),
        "listing_date":   (raw.get("listedDate") or raw.get("listing_date") or None),
    }


# ---------------------------------------------------------------------------
# PLUTO enrichment helpers
# ---------------------------------------------------------------------------

def _soda_headers() -> dict[str, str]:
    headers = {"Accept": "application/json"}
    token = os.environ.get("NYC_OPEN_DATA_APP_TOKEN")
    if token:
        headers["X-App-Token"] = token
    return headers


def fetch_pluto_batch(bbls: list[str]) -> list[dict]:
    """Fetch PLUTO rows for listings-specific fields (bldgclass, FAR, units)."""
    if not bbls:
        return []
    quoted = ", ".join(f"'{b}'" for b in bbls)
    params = {
        "$where": f"bbl IN ({quoted})",
        "$select": "bbl,bldgclass,zonedist1,residfar,builtfar,numfloors,unitsres,unitstotal",
        "$limit": len(bbls) + 10,
    }
    url = f"{PLUTO_SODA_BASE}/{PLUTO_DATASET_ID}.json"
    for attempt in range(3):
        try:
            resp = requests.get(url, params=params, headers=_soda_headers(), timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            if attempt < 2:
                wait = 2 ** attempt
                log.warning("PLUTO fetch failed (attempt %d): %s — retrying in %ds", attempt + 1, exc, wait)
                time.sleep(wait)
            else:
                log.error("PLUTO fetch failed after 3 attempts: %s", exc)
                return []
    return []


def parse_pluto_for_listing(row: dict) -> dict:
    """Extract listings-schema PLUTO fields from a raw PLUTO row."""
    residfar = _safe_float(row.get("residfar"))
    builtfar = _safe_float(row.get("builtfar"))
    far_remaining: Optional[float] = None
    if residfar is not None and builtfar is not None:
        far_remaining = round(residfar - builtfar, 3)

    return {
        "bldgclass":     (row.get("bldgclass") or "").strip() or None,
        "zonedist1":     (row.get("zonedist1") or "").strip() or None,
        "residfar":      residfar,
        "builtfar":      builtfar,
        "far_remaining": far_remaining,
        "num_floors":    _safe_float(row.get("numfloors")),
        "units_res":     _safe_int(row.get("unitsres")),
        "units_total":   _safe_int(row.get("unitstotal")),
    }


def normalize_pluto_bbl(raw_bbl: str) -> Optional[str]:
    """Normalise a PLUTO BBL like '3002750022.00000000' to '3002750022'."""
    try:
        return str(int(float(raw_bbl)))
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Main ingestion logic
# ---------------------------------------------------------------------------

def ingest(
    limit: Optional[int] = None,
    dry_run: bool = False,
    borough_filter: Optional[str] = None,
    skip_geocode: bool = False,
    cities: Optional[list[str]] = None,
    no_cache: bool = False,
) -> None:
    client = get_client()

    # Resolve optional post-fetch borough filter
    borough_name: Optional[str] = None
    if borough_filter:
        borough_name = BOROUGH_ABBREV.get(borough_filter.upper())
        if not borough_name:
            log.error("Unknown borough abbreviation: %s. Use MN, BK, BX, QN, or SI.", borough_filter)
            sys.exit(1)
        log.info("Borough filter: %s", borough_name)

    city_list = cities if cities is not None else DEFAULT_CITY_SWEEPS
    log.info("City sweeps: %s", city_list)
    log_usage_summary("rentcast")

    # ---------------------------------------------------------------------------
    # Phase 1: Fetch from RentCast — one paginated sweep per city.
    # Loads from disk cache if available so a failed upsert can be retried
    # without burning additional quota calls.
    # ---------------------------------------------------------------------------
    use_cache = not no_cache and limit is None
    cached = _load_cache(city_list) if use_cache else None
    all_listings: list[dict] = cached if cached is not None else []

    if cached is None:
        quota_exhausted = False
        for city in city_list:
            if quota_exhausted:
                break
            if limit is not None and len(all_listings) >= limit:
                break

            log.info("=== Sweeping city: %s ===", city)
            offset = 0
            page_num = 0

            while True:
                fetch_size = RENTCAST_PAGE_SIZE
                if limit is not None:
                    remaining_needed = limit - len(all_listings)
                    if remaining_needed <= 0:
                        break
                    fetch_size = min(RENTCAST_PAGE_SIZE, remaining_needed)

                page_num += 1
                log.info("Fetching %s page %d (offset=%d, size=%d)…", city, page_num, offset, fetch_size)

                try:
                    count = check_and_increment("rentcast", monthly_limit=RENTCAST_MONTHLY_LIMIT)
                    log.info("  Quota call #%d of %d this month", count, RENTCAST_MONTHLY_LIMIT)
                except QuotaExceededError as exc:
                    log.error(str(exc))
                    quota_exhausted = True
                    break

                try:
                    page = fetch_rentcast_page(offset, fetch_size, city=city)
                except requests.HTTPError as exc:
                    log.error("RentCast API error on %s page %d: %s", city, page_num, exc)
                    break
                except Exception as exc:
                    log.error("Unexpected error on %s page %d: %s", city, page_num, exc)
                    break

                if not page:
                    log.info("Empty page — end of %s results.", city)
                    break

                log.info("  Got %d listings on %s page %d", len(page), city, page_num)
                all_listings.extend(page)
                offset += len(page)

                if len(page) < fetch_size:
                    log.info("Last %s page (returned %d < %d) — stopping.", city, len(page), fetch_size)
                    break

                time.sleep(0.1)  # courtesy delay between pages

            log.info("City %s done — %d listings total so far", city, len(all_listings))

        log.info("Fetched %d total listings from RentCast across %d city sweep(s)", len(all_listings), len(city_list))

        # Save to cache so a retry can skip the API fetch
        if use_cache and all_listings:
            _save_cache(city_list, all_listings)

    if not all_listings:
        log.info("Nothing to process.")
        return

    # ---------------------------------------------------------------------------
    # Phase 2: Normalize + filter by borough
    # ---------------------------------------------------------------------------
    normalized: list[dict] = []
    for raw in all_listings:
        row = normalize_listing(raw)
        if not row["rentcast_id"] or not row["address"]:
            log.debug("Skipping listing with no id or address")
            continue
        if borough_name and row.get("borough") != borough_name:
            continue
        normalized.append(row)

    log.info("%d listings after normalization/filter", len(normalized))

    if not normalized:
        log.info("Nothing to upsert after filtering.")
        return

    # ---------------------------------------------------------------------------
    # Phase 3: Geocode for BBL (optional, slow — 1 req/s per listing)
    # ---------------------------------------------------------------------------
    if not skip_geocode:
        log.info("Geocoding %d listings for BBL (this may take a while)…", len(normalized))
        geocoded = 0
        for i, row in enumerate(normalized):
            address = row["address"]
            borough = row.get("borough") or ""
            if not address or not borough:
                continue

            lat, lng, bbl = geocode_address(address, borough)

            # Use RentCast lat/lng if GeoSearch didn't return coordinates
            if lat is not None:
                row["latitude"] = lat
                row["longitude"] = lng
            if bbl:
                row["bbl"] = bbl
                geocoded += 1

            if (i + 1) % 50 == 0:
                log.info("  Geocoded %d/%d listings…", i + 1, len(normalized))

            time.sleep(1.0)  # GeoSearch rate limit

        log.info("Got BBL for %d/%d listings", geocoded, len(normalized))

    # ---------------------------------------------------------------------------
    # Phase 4: Batch PLUTO enrichment for listings with BBL
    # ---------------------------------------------------------------------------
    if not skip_geocode:
        bbls_with_idx: list[tuple[str, int]] = [
            (row["bbl"], i)
            for i, row in enumerate(normalized)
            if row.get("bbl")
        ]
        unique_bbls = list({bbl for bbl, _ in bbls_with_idx})
        log.info("Fetching PLUTO data for %d unique BBLs…", len(unique_bbls))

        pluto_by_bbl: dict[str, dict] = {}
        total_batches = (len(unique_bbls) + PLUTO_BATCH_SIZE - 1) // PLUTO_BATCH_SIZE
        for batch_num, start in enumerate(range(0, len(unique_bbls), PLUTO_BATCH_SIZE), 1):
            batch = unique_bbls[start: start + PLUTO_BATCH_SIZE]
            log.info("  PLUTO batch %d/%d (%d BBLs)…", batch_num, total_batches, len(batch))
            pluto_rows = fetch_pluto_batch(batch)
            for pr in pluto_rows:
                raw_bbl = (pr.get("bbl") or "").strip()
                norm_bbl = normalize_pluto_bbl(raw_bbl) if raw_bbl else None
                if norm_bbl:
                    pluto_by_bbl[norm_bbl] = pr
            if batch_num < total_batches:
                time.sleep(0.2)

        log.info("PLUTO matched %d/%d BBLs", len(pluto_by_bbl), len(unique_bbls))

        # Apply PLUTO fields to normalized rows
        for row in normalized:
            bbl = row.get("bbl")
            if bbl and bbl in pluto_by_bbl:
                pluto_fields = parse_pluto_for_listing(pluto_by_bbl[bbl])
                row.update(pluto_fields)

    # ---------------------------------------------------------------------------
    # Phase 5: Upsert to Supabase
    # ---------------------------------------------------------------------------
    if dry_run:
        log.info("[DRY RUN] Would upsert %d listings. Sample:", len(normalized))
        for row in normalized[:3]:
            log.info("  %s | %s | $%s | bbl=%s | bldgclass=%s | residfar=%s",
                     row.get("rentcast_id", "?"),
                     row.get("address", "?"),
                     f"{row['price']:,}" if row.get("price") else "—",
                     row.get("bbl") or "—",
                     row.get("bldgclass") or "—",
                     row.get("residfar") or "—")
        return

    # Upsert in batches of 200 to stay within Supabase request size limits
    UPSERT_BATCH = 200
    total_upserted = 0
    for start in range(0, len(normalized), UPSERT_BATCH):
        batch = normalized[start: start + UPSERT_BATCH]
        client.table("listings").upsert(
            batch,
            on_conflict="rentcast_id",
        ).execute()
        total_upserted += len(batch)
        log.info("Upserted %d/%d listings…", total_upserted, len(normalized))

    log.info("Done. Upserted %d listings to Supabase.", total_upserted)
    _clear_cache()  # fetch succeeded — cache no longer needed


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Ingest active NYC for-sale listings from RentCast into Supabase."
    )
    parser.add_argument(
        "--limit", type=int, default=None, metavar="N",
        help="Max number of listings to fetch (default: all)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print rows without writing to the database",
    )
    parser.add_argument(
        "--borough", metavar="ABBREV", default=None,
        help="Filter by borough abbreviation: MN, BK, BX, QN, SI",
    )
    parser.add_argument(
        "--skip-geocode", action="store_true",
        help="Skip BBL geocoding and PLUTO enrichment (faster, no bbl/PLUTO fields)",
    )
    parser.add_argument(
        "--cities", nargs="+", metavar="CITY", default=None,
        help=(
            "City name(s) to query from RentCast (default: Brooklyn Bronx 'Staten Island' Queens). "
            "Pass 'New York' to re-sweep Manhattan."
        ),
    )
    parser.add_argument(
        "--no-cache", action="store_true",
        help="Ignore any existing fetch cache and always fetch fresh from RentCast.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    ingest(
        limit=args.limit,
        dry_run=args.dry_run,
        borough_filter=args.borough,
        skip_geocode=args.skip_geocode,
        cities=args.cities,
        no_cache=args.no_cache,
    )
