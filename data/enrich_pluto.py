"""PLUTO enrichment script — TES-35.

Fetches parcel data from NYC MapPLUTO (via Socrata) for every property that
has a BBL, and writes the following fields back to Supabase:

  assessed_value   DOF total assessed value        (PLUTO: assesstot)
  market_value     DOF full market value           (PLUTO: fullval)
  num_units        Residential unit count          (PLUTO: unitsres)
  num_floors       Number of floors                (PLUTO: numfloors)
  land_use         Land use code + label           (PLUTO: landuse)
  zoning_district  Primary zoning district         (PLUTO: zonedist1)

MapPLUTO dataset (NYC DCP, ~870 k parcel records):
  https://data.cityofnewyork.us/resource/64uk-42yjx.json

BBLs are looked up in batches of 100 using a Socrata `$where bbl IN (...)`
query so we never download the full dataset.

Usage:
  python data/enrich_pluto.py               # enrich all properties with a BBL
  python data/enrich_pluto.py --limit 50    # cap at 50 properties
  python data/enrich_pluto.py --dry-run     # preview without writing
  python data/enrich_pluto.py --force       # re-enrich even if already done
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from typing import Optional

import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from utils.supabase_client import get_client, fetch_all_rows  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PLUTO_DATASET_ID = "64uk-42ks"
SODA_BASE = "https://data.cityofnewyork.us/resource"
REQUEST_TIMEOUT = 30
BATCH_SIZE = 100  # max BBLs per Socrata IN() query

# PLUTO land use codes → human-readable labels
LAND_USE_LABELS: dict[str, str] = {
    "01": "01 - One & Two Family Buildings",
    "02": "02 - Multi-Family Walk-Up Buildings",
    "03": "03 - Multi-Family Elevator Buildings",
    "04": "04 - Mixed Residential & Commercial Buildings",
    "05": "05 - Commercial & Office Buildings",
    "06": "06 - Industrial & Manufacturing",
    "07": "07 - Transportation & Utility",
    "08": "08 - Public Facilities & Institutions",
    "09": "09 - Open Space & Outdoor Recreation",
    "10": "10 - Parking Facilities",
    "11": "11 - Vacant Land",
}


# ---------------------------------------------------------------------------
# Socrata helper
# ---------------------------------------------------------------------------

def _soda_headers() -> dict[str, str]:
    headers = {"Accept": "application/json"}
    token = os.environ.get("NYC_OPEN_DATA_APP_TOKEN")
    if token:
        headers["X-App-Token"] = token
    return headers


def fetch_pluto_batch(bbls: list[str]) -> list[dict]:
    """Fetch PLUTO rows for a batch of BBL strings from Socrata.

    Returns a list of raw PLUTO dicts (may be shorter than `bbls` if some
    parcels aren't in PLUTO).
    """
    if not bbls:
        return []

    quoted = ", ".join(f"'{b}'" for b in bbls)
    params = {
        "$where": f"bbl IN ({quoted})",
        "$select": "bbl,assesstot,unitsres,numfloors,landuse,zonedist1",
        "$limit": len(bbls) + 10,  # small buffer in case of duplicates
    }
    url = f"{SODA_BASE}/{PLUTO_DATASET_ID}.json"
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


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

def _safe_numeric(val: Optional[str]) -> Optional[float]:
    """Convert a Socrata string number to float, or None."""
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def _safe_int(val: Optional[str]) -> Optional[int]:
    """Convert a Socrata string number to int (rounds), or None."""
    f = _safe_numeric(val)
    return int(round(f)) if f is not None else None


def parse_pluto_row(row: dict) -> dict:
    """Extract and clean the fields we want from a raw PLUTO row."""
    land_use_code = (row.get("landuse") or "").strip().zfill(2)
    land_use_label = LAND_USE_LABELS.get(land_use_code) or (land_use_code if land_use_code else None)

    zoning = (row.get("zonedist1") or "").strip() or None

    return {
        "assessed_value": _safe_numeric(row.get("assesstot")),
        "num_units": _safe_int(row.get("unitsres")),
        "num_floors": _safe_int(row.get("numfloors")),
        "land_use": land_use_label,
        "zoning_district": zoning,
    }


# ---------------------------------------------------------------------------
# Main enrichment logic
# ---------------------------------------------------------------------------

def enrich(limit: Optional[int] = None, dry_run: bool = False, force: bool = False) -> None:
    client = get_client()

    # Load properties that have a BBL (paginate past Supabase's 1000-row limit)
    query = client.table("properties").select("id,address,borough,bbl").not_.is_("bbl", "null")
    if not force:
        # Skip rows where we've already enriched (assessed_value is a reliable proxy)
        query = query.is_("assessed_value", "null")
    if limit:
        rows = query.limit(limit).execute().data
    else:
        rows = fetch_all_rows(query)
    log.info("Found %d properties to enrich", len(rows))

    if not rows:
        log.info("Nothing to do.")
        return

    # Build BBL → property_id mapping (a BBL may appear more than once if
    # the same parcel was ingested from multiple sources)
    bbl_to_ids: dict[str, list[str]] = {}
    for row in rows:
        bbl = row["bbl"]
        bbl_to_ids.setdefault(bbl, []).append(row["id"])

    unique_bbls = list(bbl_to_ids.keys())
    log.info("Unique BBLs to look up: %d", len(unique_bbls))

    # Fetch PLUTO data in batches
    pluto_by_bbl: dict[str, dict] = {}
    total_batches = (len(unique_bbls) + BATCH_SIZE - 1) // BATCH_SIZE
    for batch_num, start in enumerate(range(0, len(unique_bbls), BATCH_SIZE), 1):
        batch = unique_bbls[start : start + BATCH_SIZE]
        log.info("Fetching PLUTO batch %d/%d (%d BBLs)…", batch_num, total_batches, len(batch))
        pluto_rows = fetch_pluto_batch(batch)
        for pr in pluto_rows:
            # PLUTO returns bbl as a float string e.g. "3002750022.00000000" — normalise to int string
            raw_bbl = str(pr.get("bbl", "")).strip()
            try:
                bbl = str(int(float(raw_bbl))) if raw_bbl else ""
            except (ValueError, TypeError):
                bbl = raw_bbl
            if bbl:
                pluto_by_bbl[bbl] = pr
        if batch_num < total_batches:
            time.sleep(0.2)  # gentle rate limiting

    log.info("PLUTO returned data for %d/%d BBLs", len(pluto_by_bbl), len(unique_bbls))

    # Apply updates
    updated = 0
    not_found = 0
    for bbl, prop_ids in bbl_to_ids.items():
        pluto_row = pluto_by_bbl.get(bbl)
        if not pluto_row:
            log.debug("  BBL %s — not found in PLUTO", bbl)
            not_found += 1
            continue

        fields = parse_pluto_row(pluto_row)
        # Drop None values so we don't overwrite existing data with nulls
        fields = {k: v for k, v in fields.items() if v is not None}
        if not fields:
            log.debug("  BBL %s — PLUTO row had no usable fields", bbl)
            not_found += 1
            continue

        for prop_id in prop_ids:
            log.info(
                "  BBL %s → %s (units=%s floors=%s zone=%s)",
                bbl,
                fields.get("land_use", "—"),
                fields.get("num_units", "—"),
                fields.get("num_floors", "—"),
                fields.get("zoning_district", "—"),
            )
            if not dry_run:
                client.table("properties").update(fields).eq("id", prop_id).execute()
                updated += 1
            else:
                updated += 1  # count for dry-run reporting

    if dry_run:
        log.info("[DRY RUN] Would have updated %d properties (%d BBLs not in PLUTO).", updated, not_found)
    else:
        log.info("Done. Updated %d properties (%d BBLs not found in PLUTO).", updated, not_found)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Enrich properties with PLUTO parcel data (assessed value, units, floors, zoning)."
    )
    parser.add_argument("--limit", type=int, default=None, metavar="N",
                        help="Max number of properties to process")
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview without writing to the database")
    parser.add_argument("--force", action="store_true",
                        help="Re-enrich properties that already have assessed_value set")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    enrich(limit=args.limit, dry_run=args.dry_run, force=args.force)
