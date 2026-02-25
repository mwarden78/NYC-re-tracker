"""ACRIS deed enrichment script — TES-36, TES-44.

For every property that has a BBL, fetches its complete deed transaction
history from three NYC ACRIS Socrata datasets, inserts the records into
the sale_history table, and back-fills properties.last_sale_price and
properties.last_sale_date with the most recent arms-length sale.

ACRIS datasets used:
  Real Property Legals:  8h5j-fqxa  — bbl → document_id pairs
  Real Property Master:  bnx9-e6tj  — document_id → document_amt, date, doc_type
  Real Property Parties: 636b-3b5g  — document_id → seller/buyer names

Arms-length deed types (sale_price > $10,000):
  DEED, DEED, RC, DEEDP, DEEDO, CONDEED, REIT, ASTU

BBLs are queried in batches of 100 via ACRIS Legals using a compound
OR filter on (borough, block, lot) — the Legals dataset has no bbl column.

Usage:
  python data/enrich_last_sale.py               # process all unprocessed properties
  python data/enrich_last_sale.py --limit 50    # cap at 50 properties
  python data/enrich_last_sale.py --dry-run     # preview without writing
  python data/enrich_last_sale.py --force       # re-ingest already-processed properties
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
ACRIS_LEGALS_ID = "8h5j-fqxa"    # Real Property Legals (bbl → document_id)
ACRIS_MASTER_ID = "bnx9-e6tj"    # Real Property Master (price, date, doc_type)
ACRIS_PARTIES_ID = "636b-3b5g"   # Real Property Parties (seller/buyer names)

REQUEST_TIMEOUT = 30
BATCH_SIZE = 100        # BBLs per Legals batch
DOC_BATCH_SIZE = 75     # document_ids per Master / Parties batch (keeps URL under Socrata limit)

# Arms-length deed types to ingest (nominal/non-market transfers are excluded)
ARMS_LENGTH_DOC_TYPES = frozenset({
    "DEED", "DEED, RC", "DEEDP", "DEEDO", "CONDEED", "REIT", "ASTU",
})
MIN_SALE_PRICE = 10_000  # filter out nominal/token consideration transfers


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
            resp = requests.get(url, params=params, headers=_soda_headers(), timeout=REQUEST_TIMEOUT)
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
    """Decompose a 10-digit BBL into (borough, block, lot) for ACRIS Legals.

    ACRIS Legals stores borough/block/lot as separate columns (no bbl column).
    BBL format: B(1) + BLOCK(5,zero-padded) + LOT(4,zero-padded)
    ACRIS stores block/lot without leading zeros, e.g. block='275', lot='22'.
    """
    borough = bbl[0]
    block = str(int(bbl[1:6]))
    lot = str(int(bbl[6:10]))
    return borough, block, lot


def fetch_document_ids_for_bbls(bbls: list[str]) -> list[dict]:
    """Step 1: get (document_id, borough, block, lot) from ACRIS Legals.

    ACRIS Legals (8h5j-fqxa) has no bbl column — it stores borough, block,
    and lot separately.  We build a compound OR filter from the decomposed BBL
    components.  Callers reconstruct the original BBL from the returned
    borough/block/lot values.
    """
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
        "$where": " OR ".join(parts),
        "$select": "document_id,borough,block,lot",
        "$limit": len(bbls) * 50,  # each parcel may have many historical deeds
    })


def fetch_deed_masters(document_ids: list[str]) -> list[dict]:
    """Step 2: get arms-length deed records from ACRIS Master for a list of document_ids.

    Uses OR conditions for doc_type instead of IN() to avoid Socrata parser
    issues with values that contain commas (e.g. 'DEED, RC').
    """
    if not document_ids:
        return []
    # Build OR filter for doc_type — avoids Socrata IN() issues with comma-containing values
    doc_type_filter = " OR ".join(f"doc_type='{t}'" for t in sorted(ARMS_LENGTH_DOC_TYPES))
    results: list[dict] = []
    for start in range(0, len(document_ids), DOC_BATCH_SIZE):
        batch = document_ids[start : start + DOC_BATCH_SIZE]
        quoted = ", ".join(f"'{d}'" for d in batch)
        rows = _soda_get(ACRIS_MASTER_ID, {
            "$where": (
                f"document_id IN ({quoted})"
                f" AND ({doc_type_filter})"
                f" AND document_amt > {MIN_SALE_PRICE}"
            ),
            "$select": "document_id,doc_type,document_amt,document_date,recorded_datetime,percent_trans",
            "$limit": len(batch) + 50,
        })
        results.extend(rows)
    return results


def fetch_parties(document_ids: list[str]) -> dict[str, dict[str, Optional[str]]]:
    """Step 3: get {document_id: {seller_name, buyer_name}} from ACRIS Parties."""
    if not document_ids:
        return {}
    result: dict[str, dict[str, Optional[str]]] = {}
    for start in range(0, len(document_ids), DOC_BATCH_SIZE):
        batch = document_ids[start : start + DOC_BATCH_SIZE]
        quoted = ", ".join(f"'{d}'" for d in batch)
        rows = _soda_get(ACRIS_PARTIES_ID, {
            "$where": f"document_id IN ({quoted}) AND party_type IN ('1', '2')",
            "$select": "document_id,party_type,name",
            "$limit": len(batch) * 4,
        })
        for row in rows:
            doc_id = row.get("document_id")
            if not doc_id:
                continue
            entry = result.setdefault(doc_id, {})
            raw_name = (row.get("name") or "").strip() or None
            if row.get("party_type") == "1" and "seller_name" not in entry:
                entry["seller_name"] = raw_name
            elif row.get("party_type") == "2" and "buyer_name" not in entry:
                entry["buyer_name"] = raw_name
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
    rows = (
        client.table("properties")
        .select("id,address,borough,bbl")
        .not_.is_("bbl", "null")
        .execute()
        .data
    )
    log.info("Total properties with BBL: %d", len(rows))

    # Unless --force, skip those that already have sale_history records
    if not force:
        existing_resp = client.table("sale_history").select("property_id").execute().data
        existing_ids = {r["property_id"] for r in existing_resp}
        rows = [r for r in rows if r["id"] not in existing_ids]
        log.info("Properties without sale history (to process): %d", len(rows))

    if limit:
        rows = rows[:limit]
        log.info("Capped at %d properties by --limit", limit)

    if not rows:
        log.info("Nothing to do.")
        return

    # Deduplicate BBLs — multiple properties can share a BBL (same parcel, different sources)
    bbl_to_ids: dict[str, list[str]] = {}
    for row in rows:
        bbl = row["bbl"]
        bbl_to_ids.setdefault(bbl, []).append(row["id"])

    unique_bbls = list(bbl_to_ids.keys())
    log.info("Unique BBLs to look up: %d", len(unique_bbls))

    # Collect sale_history rows and best-deed-per-BBL across all batches
    all_sale_rows: list[dict] = []          # for sale_history upsert
    best_deed: dict[str, dict] = {}        # bbl → {date, price, property_ids}
    total_batches = (len(unique_bbls) + BATCH_SIZE - 1) // BATCH_SIZE

    for batch_num, start in enumerate(range(0, len(unique_bbls), BATCH_SIZE), 1):
        batch_bbls = unique_bbls[start : start + BATCH_SIZE]
        log.info("Batch %d/%d: fetching legals for %d BBLs…",
                 batch_num, total_batches, len(batch_bbls))

        # Step 1: BBL → document_ids via ACRIS Legals
        legals = fetch_document_ids_for_bbls(batch_bbls)
        if not legals:
            log.info("  No legals records found for this batch")
            continue

        doc_to_bbl: dict[str, str] = {}
        for rec in legals:
            doc_id = (rec.get("document_id") or "").strip()
            # Reconstruct BBL from borough/block/lot returned by ACRIS Legals
            try:
                b = rec.get("borough", "").strip()
                bl = rec.get("block", "").strip()
                lt = rec.get("lot", "").strip()
                bbl = f"{b}{int(bl):05d}{int(lt):04d}" if b and bl and lt else ""
            except (ValueError, TypeError):
                bbl = ""
            if doc_id and bbl:
                doc_to_bbl[doc_id] = bbl

        unique_doc_ids = list(doc_to_bbl.keys())
        log.info("  %d document IDs across %d BBLs; fetching deed records…",
                 len(unique_doc_ids), len(batch_bbls))

        # Step 2: filter to arms-length deed records via ACRIS Master
        deed_records = fetch_deed_masters(unique_doc_ids)
        log.info("  %d arms-length deed record(s) found", len(deed_records))

        if not deed_records:
            continue

        deed_ids = [r["document_id"] for r in deed_records]

        # Step 3: get seller/buyer names from ACRIS Parties
        parties = fetch_parties(deed_ids)

        # Build sale_history rows and track best deed per BBL
        for rec in deed_records:
            doc_id = (rec.get("document_id") or "").strip()
            bbl = doc_to_bbl.get(doc_id)
            if not bbl:
                continue

            sale_price = _safe_numeric(rec.get("document_amt"))
            sale_date = _parse_date(rec.get("document_date"))
            party = parties.get(doc_id, {})

            # Add a sale_history row for each property sharing this BBL
            for prop_id in bbl_to_ids.get(bbl, []):
                all_sale_rows.append({
                    "property_id": prop_id,
                    "document_id": doc_id,
                    "doc_type": (rec.get("doc_type") or "").strip() or None,
                    "sale_price": sale_price,
                    "sale_date": sale_date,
                    "recorded_at": rec.get("recorded_datetime"),
                    "seller_name": party.get("seller_name"),
                    "buyer_name": party.get("buyer_name"),
                    "percent_transferred": _safe_numeric(rec.get("percent_trans")),
                    "bbl": bbl,
                })

            # Track the most recent deed per BBL for properties table update
            if sale_date:
                existing = best_deed.get(bbl)
                if existing is None or sale_date > existing["date"]:
                    best_deed[bbl] = {"date": sale_date, "price": sale_price}

        if batch_num < total_batches:
            time.sleep(0.2)

    log.info("Total sale records to upsert: %d", len(all_sale_rows))
    log.info("BBLs with at least one deed sale: %d/%d", len(best_deed), len(unique_bbls))

    if not all_sale_rows:
        log.info("No sale records found — nothing to write.")
        return

    if dry_run:
        for sr in all_sale_rows[:10]:
            log.info("  [DRY RUN] prop=%s  %-10s  $%-12s  %s",
                     sr["property_id"][:8],
                     sr["doc_type"] or "—",
                     f"{sr['sale_price']:,.0f}" if sr["sale_price"] else "—",
                     sr["sale_date"] or "—")
        if len(all_sale_rows) > 10:
            log.info("  [DRY RUN] … and %d more", len(all_sale_rows) - 10)
        log.info("[DRY RUN] Would have upserted %d sale records for %d properties.",
                 len(all_sale_rows), len(rows))
        return

    # Upsert sale_history (idempotent on property_id + document_id)
    upsert_batch = 200
    for s in range(0, len(all_sale_rows), upsert_batch):
        client.table("sale_history").upsert(
            all_sale_rows[s : s + upsert_batch],
            on_conflict="property_id,document_id",
        ).execute()
    log.info("Upserted %d sale_history records", len(all_sale_rows))

    # Back-fill properties.last_sale_price / last_sale_date
    updated = 0
    not_found = 0
    for bbl, prop_ids in bbl_to_ids.items():
        deed = best_deed.get(bbl)
        if not deed:
            not_found += 1
            continue
        fields: dict = {"last_sale_date": deed["date"]}
        if deed["price"] is not None:
            fields["last_sale_price"] = deed["price"]
        for prop_id in prop_ids:
            log.info(
                "  BBL %s → %s  $%s",
                bbl,
                deed["date"],
                f"{deed['price']:,.0f}" if deed["price"] else "—",
            )
            client.table("properties").update(fields).eq("id", prop_id).execute()
            updated += 1

    log.info(
        "Done. Upserted %d sale records. Updated last_sale on %d properties. "
        "%d BBLs had no arms-length deed records.",
        len(all_sale_rows), updated, not_found,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Enrich tracked properties with full ACRIS deed sale history."
    )
    parser.add_argument("--limit", type=int, default=None, metavar="N",
                        help="Max number of properties to process")
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview without writing to the database")
    parser.add_argument("--force", action="store_true",
                        help="Re-ingest properties that already have sale history")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    ingest(limit=args.limit, dry_run=args.dry_run, force=args.force)
