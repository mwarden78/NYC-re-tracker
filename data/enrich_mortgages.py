"""ACRIS mortgage enrichment script — TES-58.

For every property that has a BBL, fetches its mortgage history from three
NYC ACRIS Socrata datasets, inserts the records into the mortgages table,
and back-fills properties.active_mortgage_amount and
properties.active_mortgage_lender using the most recent MTGE record.

ACRIS datasets used:
  Real Property Legals:  8h5j-fqxa  — bbl → document_id pairs
  Real Property Master:  bnx9-e6tj  — document_id → amount, date, doc_type
  Real Property Parties: 636b-3b5g  — document_id → lender name (party_type=2)

Mortgage doc types ingested:
  MTGE  — Mortgage
  AGMT  — Agreement (often a building loan agreement)
  ASST  — Assignment of mortgage
  CORR  — Corrected mortgage
  MMOD  — Mortgage modification
  SMOD  — Spreader/modification
  SMXT  — Spreader/modification/extension

The most recent MTGE record per BBL is used to back-fill
active_mortgage_amount and active_mortgage_lender on the properties table.

Usage:
  python data/enrich_mortgages.py               # process all unprocessed properties
  python data/enrich_mortgages.py --limit 50    # cap at 50 properties
  python data/enrich_mortgages.py --dry-run     # preview without writing
  python data/enrich_mortgages.py --force       # re-ingest already-processed properties
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
ACRIS_LEGALS_ID  = "8h5j-fqxa"   # Real Property Legals  (bbl → document_id)
ACRIS_MASTER_ID  = "bnx9-e6tj"   # Real Property Master  (amount, date, doc_type)
ACRIS_PARTIES_ID = "636b-3b5g"   # Real Property Parties (lender name)

REQUEST_TIMEOUT = 30
BATCH_SIZE     = 100   # BBLs per Legals batch
DOC_BATCH_SIZE =  75   # document_ids per Master / Parties batch

MORTGAGE_DOC_TYPES = frozenset({
    "MTGE",   # Mortgage
    "AGMT",   # Agreement (building loan)
    "ASST",   # Assignment of mortgage
    "CORR",   # Corrected mortgage
    "MMOD",   # Mortgage modification
    "SMOD",   # Spreader / modification
    "SMXT",   # Spreader / modification / extension
})

# Only back-fill active_mortgage_* from primary mortgage originations
ACTIVE_MORTGAGE_TYPE = "MTGE"


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
    """GET a Socrata dataset with exponential-backoff retry (3 attempts)."""
    url = f"{SODA_BASE}/{dataset_id}.json"
    for attempt in range(3):
        try:
            resp = requests.get(url, params=params, headers=_soda_headers(),
                                timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            if attempt < 2:
                wait = 2 ** attempt
                log.warning("ACRIS fetch failed (attempt %d): %s — retrying in %ds",
                             attempt + 1, exc, wait)
                time.sleep(wait)
            else:
                log.error("ACRIS fetch failed after 3 attempts: %s", exc)
                return []
    return []


# ---------------------------------------------------------------------------
# ACRIS API fetchers
# ---------------------------------------------------------------------------

def _bbl_to_components(bbl: str) -> tuple[str, str, str]:
    """Decompose a 10-digit BBL into (borough, block, lot) for ACRIS Legals."""
    borough = bbl[0]
    block   = str(int(bbl[1:6]))
    lot     = str(int(bbl[6:10]))
    return borough, block, lot


def fetch_document_ids_for_bbls(bbls: list[str]) -> list[dict]:
    """Step 1: get (document_id, borough, block, lot) from ACRIS Legals."""
    parts = []
    for bbl in bbls:
        try:
            borough, block, lot = _bbl_to_components(bbl)
            parts.append(f"(borough='{borough}' AND block='{block}' AND lot='{lot}')")
        except (ValueError, IndexError):
            continue
    if not parts:
        return []
    return _soda_get(ACRIS_LEGALS_ID, {
        "$where":  " OR ".join(parts),
        "$select": "document_id,borough,block,lot",
        "$limit":  len(bbls) * 100,  # parcels can have many historical mortgages
    })


def fetch_mortgage_masters(document_ids: list[str]) -> list[dict]:
    """Step 2: get mortgage records from ACRIS Master for a list of document_ids."""
    if not document_ids:
        return []
    doc_type_filter = " OR ".join(f"doc_type='{t}'" for t in sorted(MORTGAGE_DOC_TYPES))
    results: list[dict] = []
    for start in range(0, len(document_ids), DOC_BATCH_SIZE):
        batch  = document_ids[start : start + DOC_BATCH_SIZE]
        quoted = ", ".join(f"'{d}'" for d in batch)
        rows = _soda_get(ACRIS_MASTER_ID, {
            "$where":  f"document_id IN ({quoted}) AND ({doc_type_filter})",
            "$select": "document_id,doc_type,document_amt,document_date,"
                       "good_through_date,recorded_datetime",
            "$limit":  len(batch) + 50,
        })
        results.extend(rows)
    return results


def fetch_lenders(document_ids: list[str]) -> dict[str, Optional[str]]:
    """Step 3: get {document_id: lender_name} from ACRIS Parties (party_type='2')."""
    if not document_ids:
        return {}
    result: dict[str, Optional[str]] = {}
    for start in range(0, len(document_ids), DOC_BATCH_SIZE):
        batch  = document_ids[start : start + DOC_BATCH_SIZE]
        quoted = ", ".join(f"'{d}'" for d in batch)
        rows = _soda_get(ACRIS_PARTIES_ID, {
            "$where":  f"document_id IN ({quoted}) AND party_type='2'",
            "$select": "document_id,name",
            "$limit":  len(batch) * 2,
        })
        for row in rows:
            doc_id = row.get("document_id")
            if doc_id and doc_id not in result:
                result[doc_id] = (row.get("name") or "").strip() or None
    return result


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

def _safe_numeric(val) -> Optional[float]:
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def _parse_date(val: Optional[str]) -> Optional[str]:
    """Return YYYY-MM-DD from an ACRIS ISO datetime string, or None."""
    if not val:
        return None
    return val[:10]


# ---------------------------------------------------------------------------
# Main ingestion logic
# ---------------------------------------------------------------------------

def ingest(limit: Optional[int] = None, dry_run: bool = False, force: bool = False) -> None:
    client = get_client()

    # Load all properties that have a BBL
    rows = fetch_all_rows(
        client.table("properties").select("id,address,borough,bbl").not_.is_("bbl", "null")
    )
    log.info("Total properties with BBL: %d", len(rows))

    # Unless --force, skip properties that already have mortgage records
    if not force:
        existing_resp = fetch_all_rows(client.table("mortgages").select("property_id"))
        existing_ids  = {r["property_id"] for r in existing_resp}
        rows = [r for r in rows if r["id"] not in existing_ids]
        log.info("Properties without mortgage history (to process): %d", len(rows))

    if limit:
        rows = rows[:limit]
        log.info("Capped at %d properties by --limit", limit)

    if not rows:
        log.info("Nothing to do.")
        return

    # Deduplicate BBLs
    bbl_to_ids: dict[str, list[str]] = {}
    for row in rows:
        bbl_to_ids.setdefault(row["bbl"], []).append(row["id"])

    unique_bbls    = list(bbl_to_ids.keys())
    total_batches  = (len(unique_bbls) + BATCH_SIZE - 1) // BATCH_SIZE
    log.info("Unique BBLs to look up: %d", len(unique_bbls))

    all_mortgage_rows: list[dict] = []
    # best_mtge[bbl] = {"date": ..., "amount": ..., "lender": ...}
    best_mtge: dict[str, dict] = {}

    for batch_num, start in enumerate(range(0, len(unique_bbls), BATCH_SIZE), 1):
        batch_bbls = unique_bbls[start : start + BATCH_SIZE]
        log.info("Batch %d/%d: fetching legals for %d BBLs…",
                 batch_num, total_batches, len(batch_bbls))

        # Step 1: BBL → document_ids
        legals = fetch_document_ids_for_bbls(batch_bbls)
        if not legals:
            log.info("  No legals records found for this batch")
            continue

        doc_to_bbl: dict[str, str] = {}
        for rec in legals:
            doc_id = (rec.get("document_id") or "").strip()
            try:
                b  = rec.get("borough", "").strip()
                bl = rec.get("block",   "").strip()
                lt = rec.get("lot",     "").strip()
                bbl = f"{b}{int(bl):05d}{int(lt):04d}" if b and bl and lt else ""
            except (ValueError, TypeError):
                bbl = ""
            if doc_id and bbl:
                doc_to_bbl[doc_id] = bbl

        unique_doc_ids = list(doc_to_bbl.keys())
        log.info("  %d document IDs; fetching mortgage records…", len(unique_doc_ids))

        # Step 2: filter to mortgage doc types
        mortgage_records = fetch_mortgage_masters(unique_doc_ids)
        log.info("  %d mortgage record(s) found", len(mortgage_records))

        if not mortgage_records:
            continue

        mtge_ids = [r["document_id"] for r in mortgage_records]

        # Step 3: lender names
        lenders = fetch_lenders(mtge_ids)

        for rec in mortgage_records:
            doc_id  = (rec.get("document_id") or "").strip()
            bbl     = doc_to_bbl.get(doc_id)
            if not bbl:
                continue

            doc_type       = (rec.get("doc_type") or "").strip() or None
            mortgage_amount = _safe_numeric(rec.get("document_amt"))
            mortgage_date  = _parse_date(rec.get("document_date"))
            maturity_date  = _parse_date(rec.get("good_through_date"))
            lender_name    = lenders.get(doc_id)

            for prop_id in bbl_to_ids.get(bbl, []):
                all_mortgage_rows.append({
                    "property_id":     prop_id,
                    "document_id":     doc_id,
                    "doc_type":        doc_type,
                    "lender_name":     lender_name,
                    "mortgage_amount": mortgage_amount,
                    "mortgage_date":   mortgage_date,
                    "maturity_date":   maturity_date,
                    "recorded_at":     rec.get("recorded_datetime"),
                    "bbl":             bbl,
                })

            # Track the most recent primary mortgage per BBL for back-fill
            if doc_type == ACTIVE_MORTGAGE_TYPE and mortgage_date:
                existing = best_mtge.get(bbl)
                if existing is None or mortgage_date > existing["date"]:
                    best_mtge[bbl] = {
                        "date":   mortgage_date,
                        "amount": mortgage_amount,
                        "lender": lender_name,
                    }

        if batch_num < total_batches:
            time.sleep(0.2)

    log.info("Total mortgage records to upsert: %d", len(all_mortgage_rows))
    log.info("BBLs with at least one MTGE record: %d/%d", len(best_mtge), len(unique_bbls))

    if not all_mortgage_rows:
        log.info("No mortgage records found — nothing to write.")
        return

    if dry_run:
        for mr in all_mortgage_rows[:10]:
            log.info("  [DRY RUN] prop=%s  %-6s  $%-14s  %s  lender=%s",
                     mr["property_id"][:8],
                     mr["doc_type"] or "—",
                     f"{mr['mortgage_amount']:,.0f}" if mr["mortgage_amount"] else "—",
                     mr["mortgage_date"] or "—",
                     mr["lender_name"] or "—")
        if len(all_mortgage_rows) > 10:
            log.info("  [DRY RUN] … and %d more", len(all_mortgage_rows) - 10)
        log.info("[DRY RUN] Would have upserted %d mortgage records for %d properties.",
                 len(all_mortgage_rows), len(rows))
        return

    # Upsert mortgages (idempotent on property_id + document_id)
    upsert_batch = 200
    for s in range(0, len(all_mortgage_rows), upsert_batch):
        client.table("mortgages").upsert(
            all_mortgage_rows[s : s + upsert_batch],
            on_conflict="property_id,document_id",
        ).execute()
    log.info("Upserted %d mortgage records", len(all_mortgage_rows))

    # Back-fill properties.active_mortgage_amount / active_mortgage_lender
    updated   = 0
    not_found = 0
    for bbl, prop_ids in bbl_to_ids.items():
        mtge = best_mtge.get(bbl)
        if not mtge:
            not_found += 1
            continue
        fields: dict = {}
        if mtge["amount"] is not None:
            fields["active_mortgage_amount"] = mtge["amount"]
        if mtge["lender"] is not None:
            fields["active_mortgage_lender"] = mtge["lender"]
        if not fields:
            not_found += 1
            continue
        for prop_id in prop_ids:
            log.info(
                "  BBL %s → %s  $%s  lender=%s",
                bbl,
                mtge["date"],
                f"{mtge['amount']:,.0f}" if mtge["amount"] else "—",
                mtge["lender"] or "—",
            )
            client.table("properties").update(fields).eq("id", prop_id).execute()
            updated += 1

    log.info(
        "Done. Upserted %d mortgage records. Updated active_mortgage on %d properties. "
        "%d BBLs had no MTGE records.",
        len(all_mortgage_rows), updated, not_found,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Enrich tracked properties with ACRIS mortgage history."
    )
    parser.add_argument("--limit", type=int, default=None, metavar="N",
                        help="Max number of properties to process")
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview without writing to the database")
    parser.add_argument("--force", action="store_true",
                        help="Re-ingest properties that already have mortgage records")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    ingest(limit=args.limit, dry_run=args.dry_run, force=args.force)
