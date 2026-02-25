"""Lien amount enrichment script — TES-41.

For every tax-lien property that has a BBL, looks up lien documents in
ACRIS and writes back:

  lien_amount   NUMERIC   most recent (or largest) lien document amount
  lien_doc_type TEXT      ACRIS doc_type of the matched lien record

Source: ACRIS Real Property Master (bnx9-e6tj) cross-referenced via
ACRIS Real Property Legals (8h5j-fqxa) by BBL.  The DOF Tax Lien Sale
List (9rz4-mjek) does not carry dollar amounts — this is the best
available public source.

Lien doc_type filter (arms-length tax/water/sewer liens only):
  LIEN    — general lien (most NYC tax lien sale assignments land here)
  LTAX    — tax lien
  WATL    — water lien
  SEWL    — sewer lien
  LIEN EX — lien extension

Strategy: for each BBL, pick the *most recent* lien document whose
amount > 0.  If a property has multiple active lien appearances the
most recent one is the best proxy for the outstanding balance.

Usage:
  python data/enrich_lien_amount.py               # all tax-lien BBL properties
  python data/enrich_lien_amount.py --limit 50    # cap at 50 properties
  python data/enrich_lien_amount.py --dry-run     # preview without writing
  python data/enrich_lien_amount.py --force       # re-enrich already-done rows
  python data/enrich_lien_amount.py --all-deals   # include non-tax-lien properties
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

SODA_BASE = "https://data.cityofnewyork.us/resource"
ACRIS_MASTER_ID = "bnx9-e6tj"   # Real Property Master (doc_type, date, amount)
ACRIS_LEGALS_ID = "8h5j-fqxa"  # Real Property Legals (bbl → document_id)
REQUEST_TIMEOUT = 30
BATCH_SIZE = 100  # BBLs per Socrata query

# ACRIS doc_types that represent tax / water / sewer liens
LIEN_DOC_TYPES = ("LIEN", "LTAX", "WATL", "SEWL", "LIEN EX")


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
            resp = requests.get(
                url, params=params, headers=_soda_headers(), timeout=REQUEST_TIMEOUT
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            if attempt < 2:
                wait = 2 ** attempt
                log.warning(
                    "Socrata fetch failed (attempt %d): %s — retrying in %ds",
                    attempt + 1, exc, wait,
                )
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
        "$limit": len(bbls) * 50,
    })


def fetch_lien_records(document_ids: list[str]) -> list[dict]:
    """Step 2: get lien records from ACRIS Master for a list of document_ids."""
    if not document_ids:
        return []
    quoted_ids = ", ".join(f"'{d}'" for d in document_ids)
    quoted_types = ", ".join(f"'{t}'" for t in LIEN_DOC_TYPES)
    return _soda_get(ACRIS_MASTER_ID, {
        "$where": (
            f"document_id IN ({quoted_ids})"
            f" AND doc_type IN ({quoted_types})"
            f" AND document_amt > 0"
        ),
        "$select": "document_id,doc_type,document_date,document_amt",
        "$limit": len(document_ids) + 50,
    })


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

def _parse_date(val: Optional[str]) -> Optional[str]:
    """Return ISO date string (YYYY-MM-DD) from an ACRIS date field, or None."""
    if not val:
        return None
    try:
        return val[:10]
    except Exception:
        return None


def _parse_amount(val: Optional[str]) -> Optional[float]:
    """Parse a Socrata numeric string to float, or None."""
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Main enrichment logic
# ---------------------------------------------------------------------------

def enrich(
    limit: Optional[int] = None,
    dry_run: bool = False,
    force: bool = False,
    all_deals: bool = False,
) -> None:
    client = get_client()

    # Load properties with a BBL
    query = (
        client.table("properties")
        .select("id,address,borough,bbl,deal_type")
        .not_.is_("bbl", "null")
    )
    if not all_deals:
        query = query.eq("deal_type", "tax_lien")
    if not force:
        query = query.is_("lien_amount", "null")
    rows = query.limit(limit).execute().data if limit else fetch_all_rows(query)
    log.info("Found %d properties to enrich with lien amount data", len(rows))

    if not rows:
        log.info("Nothing to do.")
        return

    # Deduplicate BBLs
    bbl_to_ids: dict[str, list[str]] = {}
    for row in rows:
        bbl_to_ids.setdefault(row["bbl"], []).append(row["id"])

    unique_bbls = list(bbl_to_ids.keys())
    log.info("Unique BBLs to look up: %d", len(unique_bbls))

    # Process in batches
    best_lien: dict[str, dict] = {}  # bbl → {amount, doc_type, date}
    total_batches = (len(unique_bbls) + BATCH_SIZE - 1) // BATCH_SIZE

    for batch_num, start in enumerate(range(0, len(unique_bbls), BATCH_SIZE), 1):
        batch_bbls = unique_bbls[start: start + BATCH_SIZE]
        log.info(
            "Batch %d/%d: fetching legals for %d BBLs...",
            batch_num, total_batches, len(batch_bbls),
        )

        # Step 1: BBL → document_ids
        legals = fetch_document_ids_for_bbls(batch_bbls)
        if not legals:
            log.info("  No legals records found for this batch")
            continue

        doc_to_bbl: dict[str, str] = {}
        for rec in legals:
            doc_id = (rec.get("document_id") or "").strip()
            bbl = (rec.get("bbl") or "").strip()
            if doc_id and bbl:
                doc_to_bbl[doc_id] = bbl

        unique_doc_ids = list(doc_to_bbl.keys())
        log.info(
            "  Found %d document IDs across %d BBLs; fetching lien records...",
            len(unique_doc_ids), len(batch_bbls),
        )

        # Step 2: document_ids → lien records (sub-batch if large)
        liens: list[dict] = []
        doc_batch_size = 500
        for doc_start in range(0, len(unique_doc_ids), doc_batch_size):
            liens.extend(
                fetch_lien_records(unique_doc_ids[doc_start: doc_start + doc_batch_size])
            )

        log.info("  Lien records returned: %d", len(liens))

        # Keep the most recent lien per BBL
        for lien in liens:
            doc_id = (lien.get("document_id") or "").strip()
            bbl = doc_to_bbl.get(doc_id)
            if not bbl:
                continue

            lien_date = _parse_date(lien.get("document_date"))
            lien_amount = _parse_amount(lien.get("document_amt"))
            doc_type = (lien.get("doc_type") or "").strip()

            if not lien_amount or lien_amount <= 0:
                continue

            existing = best_lien.get(bbl)
            if existing is None or (lien_date or "") > (existing.get("date") or ""):
                best_lien[bbl] = {
                    "amount": lien_amount,
                    "doc_type": doc_type,
                    "date": lien_date,
                }

        if batch_num < total_batches:
            time.sleep(0.2)

    log.info(
        "Found lien data for %d/%d unique BBLs", len(best_lien), len(unique_bbls)
    )

    # Apply updates to Supabase
    updated = 0
    not_found = 0
    for bbl, prop_ids in bbl_to_ids.items():
        lien = best_lien.get(bbl)
        if not lien:
            not_found += 1
            continue

        fields: dict = {
            "lien_amount": lien["amount"],
            "lien_doc_type": lien["doc_type"],
        }

        for prop_id in prop_ids:
            log.info(
                "  BBL %s → %s  $%s  (%s)",
                bbl,
                lien.get("date", "—"),
                f"{lien['amount']:,.0f}",
                lien["doc_type"],
            )
            if not dry_run:
                client.table("properties").update(fields).eq("id", prop_id).execute()
                updated += 1
            else:
                updated += 1

    if dry_run:
        log.info(
            "[DRY RUN] Would have updated %d properties (%d BBLs with no lien records).",
            updated, not_found,
        )
    else:
        log.info(
            "Done. Updated %d properties (%d BBLs with no lien records found).",
            updated, not_found,
        )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Enrich tax-lien properties with lien amounts from ACRIS."
    )
    parser.add_argument(
        "--limit", type=int, default=None, metavar="N",
        help="Max number of properties to process",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Preview without writing to the database",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Re-enrich properties that already have lien_amount set",
    )
    parser.add_argument(
        "--all-deals", action="store_true",
        help="Process all deal types, not just tax_lien (useful for ACRIS lien lookups on foreclosures)",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    enrich(
        limit=args.limit,
        dry_run=args.dry_run,
        force=args.force,
        all_deals=args.all_deals,
    )
