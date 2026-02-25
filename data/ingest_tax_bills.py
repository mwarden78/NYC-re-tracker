"""DOF Property Tax Bill enrichment script — TES-56.

Fetches outstanding tax charges and arrears from the NYC Department of Finance
Property Charges Balance dataset (Socrata) for every property that has a BBL,
and writes the following fields back to Supabase:

  tax_arrears    Sum of all outstanding balances (sum_bal > 0) across all charge records
  annual_tax     Sum of standard tax charges (code='CHG') for the most recent tax year
  tax_bill_date  Most recent charge update date (up_date)

Data source: DOF Property Charges Balance (NYC Open Data, dataset scjx-j6np)
  https://data.cityofnewyork.us/City-Government/DOF-Property-Charges-Balance/scjx-j6np

BBLs are matched against the `parid` field, which uses the same 10-digit format
as our `bbl` column (1 borough digit + 5-digit block + 4-digit lot, no separators).

Usage:
  python data/ingest_tax_bills.py               # enrich all BBL-bearing properties
  python data/ingest_tax_bills.py --limit 50    # cap at 50 properties
  python data/ingest_tax_bills.py --dry-run     # preview without writing
  python data/ingest_tax_bills.py --force       # re-enrich already-enriched properties
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

TAX_CHARGES_DATASET_ID = "scjx-j6np"
SODA_BASE = "https://data.cityofnewyork.us/resource"
REQUEST_TIMEOUT = 30
BATCH_SIZE = 100   # BBLs per Socrata IN() query


# ---------------------------------------------------------------------------
# Socrata helper
# ---------------------------------------------------------------------------

def _soda_headers() -> dict[str, str]:
    headers = {"Accept": "application/json"}
    token = os.environ.get("NYC_OPEN_DATA_APP_TOKEN")
    if token:
        headers["X-App-Token"] = token
    return headers


def fetch_tax_charges_batch(bbls: list[str]) -> list[dict]:
    """Fetch all charge rows for a batch of BBLs from the DOF charges dataset.

    Returns a flat list of raw charge dicts (many rows per BBL).
    """
    if not bbls:
        return []

    quoted = ", ".join(f"'{b}'" for b in bbls)
    params = {
        "$where": f"parid IN ({quoted})",
        "$select": "parid,code,sum_liab,sum_bal,taxyear,up_date",
        "$limit": len(bbls) * 50,  # up to ~50 charge rows per property
    }
    url = f"{SODA_BASE}/{TAX_CHARGES_DATASET_ID}.json"
    for attempt in range(3):
        try:
            resp = requests.get(url, params=params, headers=_soda_headers(), timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            if attempt < 2:
                wait = 2 ** attempt
                log.warning("Tax charges fetch failed (attempt %d): %s — retrying in %ds", attempt + 1, exc, wait)
                time.sleep(wait)
            else:
                log.error("Tax charges fetch failed after 3 attempts: %s", exc)
                return []
    return []


# ---------------------------------------------------------------------------
# Aggregation helpers
# ---------------------------------------------------------------------------

def _safe_float(val) -> Optional[float]:
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def aggregate_tax_charges(rows: list[dict]) -> dict:
    """Aggregate raw charge rows for a single BBL into our three fields.

    Returns a dict with tax_arrears, annual_tax, tax_bill_date.
    All values may be None if the rows contain no useful data.
    """
    if not rows:
        return {"tax_arrears": None, "annual_tax": None, "tax_bill_date": None}

    # Total outstanding balance across all charge types and years
    total_arrears = 0.0
    has_arrears = False
    for row in rows:
        bal = _safe_float(row.get("sum_bal"))
        if bal is not None and bal > 0:
            total_arrears += bal
            has_arrears = True

    # Annual tax = sum of standard CHG charges for the most recent tax year
    chg_rows = [r for r in rows if (r.get("code") or "").upper() == "CHG"]
    annual_tax: Optional[float] = None
    if chg_rows:
        # Find the most recent tax year in CHG rows
        years = [r.get("taxyear") for r in chg_rows if r.get("taxyear")]
        if years:
            max_year = max(years)
            current_year_chg = [r for r in chg_rows if r.get("taxyear") == max_year]
            year_total = sum((_safe_float(r.get("sum_liab")) or 0.0) for r in current_year_chg)
            if year_total > 0:
                annual_tax = year_total

    # Most recent update date across all rows
    tax_bill_date: Optional[str] = None
    dates = [r.get("up_date") for r in rows if r.get("up_date")]
    if dates:
        raw_date = max(dates)
        # Socrata returns ISO datetime strings like "2025-11-17T00:00:00.000"
        tax_bill_date = raw_date[:10]  # keep YYYY-MM-DD

    return {
        "tax_arrears": round(total_arrears, 2) if has_arrears else None,
        "annual_tax": round(annual_tax, 2) if annual_tax is not None else None,
        "tax_bill_date": tax_bill_date,
    }


# ---------------------------------------------------------------------------
# Main enrichment logic
# ---------------------------------------------------------------------------

def enrich(limit: Optional[int] = None, dry_run: bool = False, force: bool = False) -> None:
    client = get_client()

    query = client.table("properties").select("id,address,borough,bbl").not_.is_("bbl", "null")
    if not force:
        query = query.is_("tax_arrears", "null")
    if limit:
        rows = query.limit(limit).execute().data
    else:
        rows = fetch_all_rows(query)

    log.info("Found %d properties to enrich", len(rows))
    if not rows:
        log.info("Nothing to do.")
        return

    # Deduplicate BBLs (same parcel may have been ingested from multiple sources)
    bbl_to_ids: dict[str, list[str]] = {}
    for row in rows:
        bbl = row["bbl"]
        bbl_to_ids.setdefault(bbl, []).append(row["id"])

    unique_bbls = list(bbl_to_ids.keys())
    log.info("Unique BBLs to look up: %d", len(unique_bbls))

    # Fetch charge data in batches and group rows by BBL
    charges_by_bbl: dict[str, list[dict]] = {}
    total_batches = (len(unique_bbls) + BATCH_SIZE - 1) // BATCH_SIZE
    for batch_num, start in enumerate(range(0, len(unique_bbls), BATCH_SIZE), 1):
        batch = unique_bbls[start : start + BATCH_SIZE]
        log.info("Fetching tax charges batch %d/%d (%d BBLs)…", batch_num, total_batches, len(batch))
        charge_rows = fetch_tax_charges_batch(batch)
        for cr in charge_rows:
            parid = (cr.get("parid") or "").strip()
            if parid:
                charges_by_bbl.setdefault(parid, []).append(cr)
        if batch_num < total_batches:
            time.sleep(0.2)

    log.info("DOF returned charge data for %d/%d BBLs", len(charges_by_bbl), len(unique_bbls))

    # Apply updates
    updated = 0
    not_found = 0
    for bbl, prop_ids in bbl_to_ids.items():
        charge_rows = charges_by_bbl.get(bbl)
        if not charge_rows:
            log.debug("  BBL %s — no charge data found", bbl)
            not_found += 1
            continue

        fields = aggregate_tax_charges(charge_rows)
        # Drop None values so we don't overwrite existing data with nulls
        fields = {k: v for k, v in fields.items() if v is not None}
        if not fields:
            log.debug("  BBL %s — no usable charge data", bbl)
            not_found += 1
            continue

        arrears_str = f"${fields['tax_arrears']:,.0f}" if fields.get("tax_arrears") else "—"
        annual_str = f"${fields['annual_tax']:,.0f}" if fields.get("annual_tax") else "—"
        log.info("  BBL %s — arrears=%s annual_tax=%s date=%s",
                 bbl, arrears_str, annual_str, fields.get("tax_bill_date", "—"))

        if not dry_run:
            for prop_id in prop_ids:
                client.table("properties").update(fields).eq("id", prop_id).execute()
        updated += len(prop_ids)

    if dry_run:
        log.info("[DRY RUN] Would have updated %d properties (%d BBLs not found).", updated, not_found)
    else:
        log.info("Done. Updated %d properties (%d BBLs with no charge data).", updated, not_found)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Enrich properties with DOF property tax bill data (arrears, annual tax)."
    )
    parser.add_argument("--limit", type=int, default=None, metavar="N",
                        help="Max number of properties to process")
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview without writing to the database")
    parser.add_argument("--force", action="store_true",
                        help="Re-enrich properties that already have tax_arrears set")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    enrich(limit=args.limit, dry_run=args.dry_run, force=args.force)
