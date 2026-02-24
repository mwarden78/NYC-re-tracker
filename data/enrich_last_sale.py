"""ACRIS DEED last-sale enrichment script — TES-36.

For every property that has a BBL, looks up its most recent recorded sale
in ACRIS and writes back:

  last_sale_price   NUMERIC   most recent DEED document amount
  last_sale_date    DATE      most recent DEED document date

How it works (two-step Socrata query per batch):

  Step 1 — ACRIS Legals (8h5j-fqxa):
    WHERE bbl IN (<batch of BBLs>)
    → yields (document_id, bbl) pairs

  Step 2 — ACRIS Master (bnx9-e6tj):
    WHERE document_id IN (<ids from step 1>) AND doc_type = 'DEED'
    → yields (document_id, document_date, docamount)

  In Python: join on document_id, keep the most recent DEED per BBL,
  then update Supabase.

Usage:
  python data/enrich_last_sale.py               # enrich all BBL-having properties
  python data/enrich_last_sale.py --limit 50    # cap at 50 properties
  python data/enrich_last_sale.py --dry-run     # preview without writing
  python data/enrich_last_sale.py --force       # re-enrich already-done rows
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from datetime import date
from typing import Optional

import requests

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
ACRIS_MASTER_ID = "bnx9-e6tj"   # Real Property Master (doc_type, date, amount)
ACRIS_LEGALS_ID = "8h5j-fqxa"  # Real Property Legals (bbl → document_id)
REQUEST_TIMEOUT = 30
BATCH_SIZE = 100  # BBLs per batch


# ---------------------------------------------------------------------------
# Socrata helpers
# ---------------------------------------------------------------------------

def _soda_headers() -> dict[str, str]:
    headers = {"Accept": "application/json"}
    token = os.environ.get("NYC_OPEN_DATA_APP_TOKEN")
    if token:
        headers["X-App-Token"] = token
    return headers


def _soda_get(dataset_id: str, params: dict) -> list[dict]:
    url = f"{SODA_BASE}/{dataset_id}.json"
    for attempt in range(3):
        try:
            resp = requests.get(url, params=params, headers=_soda_headers(), timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            if attempt < 2:
                wait = 2 ** attempt
                log.warning("Socrata fetch failed (attempt %d): %s — retrying in %ds", attempt + 1, exc, wait)
                time.sleep(wait)
            else:
                log.error("Socrata fetch failed after 3 attempts: %s", exc)
                return []
    return []


# ---------------------------------------------------------------------------
# ACRIS query helpers
# ---------------------------------------------------------------------------

def fetch_document_ids_for_bbls(bbls: list[str]) -> list[dict]:
    """Step 1: get (document_id, bbl) pairs from ACRIS Legals for a batch of BBLs."""
    quoted = ", ".join(f"'{b}'" for b in bbls)
    return _soda_get(ACRIS_LEGALS_ID, {
        "$where": f"bbl IN ({quoted})",
        "$select": "document_id,bbl",
        "$limit": len(bbls) * 50,  # each BBL may have many deeds over time
    })


def fetch_deed_records(document_ids: list[str]) -> list[dict]:
    """Step 2: get DEED records from ACRIS Master for a list of document_ids."""
    if not document_ids:
        return []
    quoted = ", ".join(f"'{d}'" for d in document_ids)
    return _soda_get(ACRIS_MASTER_ID, {
        "$where": f"document_id IN ({quoted}) AND doc_type='DEED'",
        "$select": "document_id,document_date,docamount",
        "$limit": len(document_ids) + 50,
    })


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

def _parse_date(val: Optional[str]) -> Optional[str]:
    """Return ISO date string (YYYY-MM-DD) from an ACRIS date field, or None."""
    if not val:
        return None
    # ACRIS returns ISO 8601 timestamps like '2019-06-15T00:00:00.000'
    try:
        return val[:10]  # take the date portion
    except Exception:
        return None


def _parse_amount(val: Optional[str]) -> Optional[float]:
    """Parse a Socrata numeric string to float, or None."""
    if val is None:
        return None
    try:
        amount = float(val)
        # Filter out $0 and $1 (nominal/quitclaim transfers — not real market sales)
        return amount if amount > 1 else None
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Main enrichment logic
# ---------------------------------------------------------------------------

def enrich(limit: Optional[int] = None, dry_run: bool = False, force: bool = False) -> None:
    client = get_client()

    # Load properties with a BBL
    query = (
        client.table("properties")
        .select("id,address,borough,bbl")
        .not_.is_("bbl", "null")
    )
    if not force:
        # Skip rows already enriched (last_sale_date is a reliable proxy)
        query = query.is_("last_sale_date", "null")
    if limit:
        query = query.limit(limit)

    rows = query.execute().data
    log.info("Found %d properties to enrich with last-sale data", len(rows))

    if not rows:
        log.info("Nothing to do.")
        return

    # Deduplicate BBLs — multiple properties can share a BBL (same parcel, diff sources)
    bbl_to_ids: dict[str, list[str]] = {}
    for row in rows:
        bbl_to_ids.setdefault(row["bbl"], []).append(row["id"])

    unique_bbls = list(bbl_to_ids.keys())
    log.info("Unique BBLs to look up: %d", len(unique_bbls))

    # Process in batches
    best_deed: dict[str, dict] = {}  # bbl → {date, amount}
    total_batches = (len(unique_bbls) + BATCH_SIZE - 1) // BATCH_SIZE

    for batch_num, start in enumerate(range(0, len(unique_bbls), BATCH_SIZE), 1):
        batch_bbls = unique_bbls[start : start + BATCH_SIZE]
        log.info("Batch %d/%d: fetching legals for %d BBLs…", batch_num, total_batches, len(batch_bbls))

        # Step 1: BBL → document_ids
        legals = fetch_document_ids_for_bbls(batch_bbls)
        if not legals:
            log.info("  No legals records found for this batch")
            continue

        # Build document_id → bbl lookup and collect unique doc IDs
        doc_to_bbl: dict[str, str] = {}
        for rec in legals:
            doc_id = (rec.get("document_id") or "").strip()
            bbl = (rec.get("bbl") or "").strip()
            if doc_id and bbl:
                doc_to_bbl[doc_id] = bbl

        unique_doc_ids = list(doc_to_bbl.keys())
        log.info("  Found %d document IDs across %d BBLs; fetching DEED records…",
                 len(unique_doc_ids), len(batch_bbls))

        # Step 2: document_ids → DEED records (may need sub-batching if many docs)
        deeds: list[dict] = []
        doc_batch_size = 500
        for doc_start in range(0, len(unique_doc_ids), doc_batch_size):
            deeds.extend(fetch_deed_records(unique_doc_ids[doc_start : doc_start + doc_batch_size]))

        log.info("  DEED records returned: %d", len(deeds))

        # Keep the most recent DEED per BBL
        for deed in deeds:
            doc_id = (deed.get("document_id") or "").strip()
            bbl = doc_to_bbl.get(doc_id)
            if not bbl:
                continue

            sale_date = _parse_date(deed.get("document_date"))
            sale_amount = _parse_amount(deed.get("docamount"))

            if not sale_date:
                continue

            existing = best_deed.get(bbl)
            if existing is None or sale_date > existing["date"]:
                best_deed[bbl] = {"date": sale_date, "amount": sale_amount}

        if batch_num < total_batches:
            time.sleep(0.2)

    log.info("Found last-sale data for %d/%d unique BBLs", len(best_deed), len(unique_bbls))

    # Apply updates to Supabase
    updated = 0
    not_found = 0
    for bbl, prop_ids in bbl_to_ids.items():
        deed = best_deed.get(bbl)
        if not deed:
            not_found += 1
            continue

        fields: dict = {"last_sale_date": deed["date"]}
        if deed["amount"] is not None:
            fields["last_sale_price"] = deed["amount"]

        for prop_id in prop_ids:
            log.info(
                "  BBL %s → %s  $%s",
                bbl,
                deed["date"],
                f"{deed['amount']:,.0f}" if deed["amount"] else "—",
            )
            if not dry_run:
                client.table("properties").update(fields).eq("id", prop_id).execute()
                updated += 1
            else:
                updated += 1

    if dry_run:
        log.info("[DRY RUN] Would have updated %d properties (%d BBLs with no DEED records).",
                 updated, not_found)
    else:
        log.info("Done. Updated %d properties (%d BBLs with no DEED records found).",
                 updated, not_found)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Enrich properties with last recorded sale from ACRIS DEED records."
    )
    parser.add_argument("--limit", type=int, default=None, metavar="N",
                        help="Max number of properties to process")
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview without writing to the database")
    parser.add_argument("--force", action="store_true",
                        help="Re-enrich properties that already have last_sale_date set")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    enrich(limit=args.limit, dry_run=args.dry_run, force=args.force)
