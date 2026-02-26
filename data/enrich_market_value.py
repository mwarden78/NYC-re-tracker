"""DOF Property Valuation enrichment — backfill market_value.

Fetches the DOF full market value (curmkttot) from the NYC DOF Property
Valuation and Assessment dataset (Tax Classes 1,2,3,4) and writes it to
properties.market_value in Supabase.

Dataset: Property Valuation and Assessment Data Tax Classes 1,2,3,4
Socrata ID: 8y4t-faws
URL: https://data.cityofnewyork.us/City-Government/Property-Valuation-and-Assessment-Data-Tax-Classes/8y4t-faws

The dataset uses `parid` (10-digit BBL) as the parcel identifier. We query
the most recent tax year with period=1 (tentative) to get current values.

Fields written:
  market_value  ← curmkttot  (current full market value total)

Usage:
  python data/enrich_market_value.py               # enrich all with BBL
  python data/enrich_market_value.py --limit 50    # cap at 50 properties
  python data/enrich_market_value.py --dry-run     # preview without writing
  python data/enrich_market_value.py --force       # re-enrich already-set rows
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

DOF_VALUATION_ID = "8y4t-faws"
SODA_BASE = "https://data.cityofnewyork.us/resource"
REQUEST_TIMEOUT = 30
BATCH_SIZE = 100  # BBLs per Socrata IN() query


# ---------------------------------------------------------------------------
# Socrata helper
# ---------------------------------------------------------------------------

def _soda_headers() -> dict[str, str]:
    headers = {"Accept": "application/json"}
    token = os.environ.get("NYC_OPEN_DATA_APP_TOKEN")
    if token:
        headers["X-App-Token"] = token
    return headers


def _soda_get(dataset_id: str, params: dict) -> list[dict]:
    """GET a Socrata dataset with exponential-backoff retry (3 attempts)."""
    url = f"{SODA_BASE}/{dataset_id}.json"
    for attempt in range(3):
        try:
            resp = requests.get(url, params=params, headers=_soda_headers(), timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            if attempt < 2:
                wait = 2 ** attempt
                log.warning("DOF fetch failed (attempt %d): %s — retrying in %ds",
                            attempt + 1, exc, wait)
                time.sleep(wait)
            else:
                log.error("DOF fetch failed after 3 attempts: %s", exc)
                return []
    return []


# ---------------------------------------------------------------------------
# Fetch helpers
# ---------------------------------------------------------------------------

def _detect_latest_year() -> str:
    """Query the dataset for the most recent tax year."""
    rows = _soda_get(DOF_VALUATION_ID, {
        "$select": "year",
        "$order": "year DESC",
        "$limit": 1,
    })
    if rows:
        return rows[0]["year"]
    return "2027"  # fallback


def fetch_valuations_batch(bbls: list[str], tax_year: str) -> list[dict]:
    """Fetch DOF valuation rows for a batch of BBLs (as parid) for a given year."""
    if not bbls:
        return []
    quoted = ", ".join(f"'{b}'" for b in bbls)
    return _soda_get(DOF_VALUATION_ID, {
        "$where": f"parid IN ({quoted}) AND year='{tax_year}' AND period='1'",
        "$select": "parid,curmkttot,curacttot",
        "$limit": len(bbls) + 10,
    })


def _safe_numeric(val) -> Optional[float]:
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Main enrichment logic
# ---------------------------------------------------------------------------

def enrich(limit: Optional[int] = None, dry_run: bool = False, force: bool = False) -> None:
    client = get_client()

    # Detect latest tax year in the dataset
    tax_year = _detect_latest_year()
    log.info("Using DOF tax year: %s", tax_year)

    # Load properties that have a BBL
    def _props_query():
        q = client.table("properties").select("id,address,borough,bbl").not_.is_("bbl", "null")
        if not force:
            q = q.is_("market_value", "null")
        return q

    rows = _props_query().limit(limit).execute().data if limit else fetch_all_rows(_props_query)
    log.info("Found %d properties to enrich", len(rows))

    if not rows:
        log.info("Nothing to do.")
        return

    # Build BBL → property_id mapping
    bbl_to_ids: dict[str, list[str]] = {}
    for row in rows:
        bbl = row["bbl"]
        bbl_to_ids.setdefault(bbl, []).append(row["id"])

    unique_bbls = list(bbl_to_ids.keys())
    log.info("Unique BBLs to look up: %d", len(unique_bbls))

    # Fetch in batches
    dof_by_bbl: dict[str, dict] = {}
    total_batches = (len(unique_bbls) + BATCH_SIZE - 1) // BATCH_SIZE
    for batch_num, start in enumerate(range(0, len(unique_bbls), BATCH_SIZE), 1):
        batch = unique_bbls[start : start + BATCH_SIZE]
        log.info("Fetching DOF batch %d/%d (%d BBLs)…", batch_num, total_batches, len(batch))
        rows_dof = fetch_valuations_batch(batch, tax_year)
        for r in rows_dof:
            parid = (r.get("parid") or "").strip()
            if parid:
                dof_by_bbl[parid] = r
        if batch_num < total_batches:
            time.sleep(0.2)

    log.info("DOF returned data for %d/%d BBLs", len(dof_by_bbl), len(unique_bbls))

    # Apply updates
    updated = 0
    not_found = 0
    for bbl, prop_ids in bbl_to_ids.items():
        dof_row = dof_by_bbl.get(bbl)
        if not dof_row:
            not_found += 1
            continue

        market_val = _safe_numeric(dof_row.get("curmkttot"))
        if market_val is None or market_val <= 0:
            not_found += 1
            continue

        fields = {"market_value": market_val}

        for prop_id in prop_ids:
            log.info("  BBL %s → market_value=$%s", bbl, f"{market_val:,.0f}")
            if not dry_run:
                client.table("properties").update(fields).eq("id", prop_id).execute()
            updated += 1

    if dry_run:
        log.info("[DRY RUN] Would have updated %d properties (%d BBLs not in DOF).", updated, not_found)
    else:
        log.info("Done. Updated %d properties (%d BBLs not found in DOF).", updated, not_found)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Enrich properties with DOF full market value."
    )
    parser.add_argument("--limit", type=int, default=None, metavar="N",
                        help="Max number of properties to process")
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview without writing to the database")
    parser.add_argument("--force", action="store_true",
                        help="Re-enrich properties that already have market_value set")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    enrich(limit=args.limit, dry_run=args.dry_run, force=args.force)
