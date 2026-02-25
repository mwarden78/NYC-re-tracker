"""ingest_lien_history.py — TES-40

Fetches prior tax lien history from NYC Open Data (dataset 9rz4-mjek)
for every property in the `properties` table that has a block + lot + borough
and upserts records into the `lien_history` table.

Usage:
    python data/ingest_lien_history.py [--limit N] [--dry-run]

Flags:
    --limit N     Only process the first N properties (useful for testing).
    --dry-run     Fetch and parse records but do not write to Supabase.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from typing import Any

import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from utils.supabase_client import get_client  # noqa: E402

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

# NYC Open Data: DOF Tax Lien Sale – Tax Lien List
LIEN_DATASET_ID = "9rz4-mjek"
SODA_BASE = f"https://data.cityofnewyork.us/resource/{LIEN_DATASET_ID}.json"
PAGE_SIZE = 1000

# Map borough name → borough digit used in BBL
BOROUGH_DIGIT: dict[str, str] = {
    "Manhattan": "1",
    "Bronx": "2",
    "Brooklyn": "3",
    "Queens": "4",
    "Staten Island": "5",
}

# Polite delay between SODA calls (seconds)
REQUEST_DELAY = 0.25


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_bbl(borough: str, block: str, lot: str) -> str | None:
    """Return 10-digit BBL string or None if inputs are invalid."""
    digit = BOROUGH_DIGIT.get(borough)
    if not digit or not block or not lot:
        return None
    try:
        block_pad = str(int(block)).zfill(5)
        lot_pad = str(int(lot)).zfill(4)
    except (ValueError, TypeError):
        return None
    return f"{digit}{block_pad}{lot_pad}"


def _source_row_id(row: dict[str, Any]) -> str:
    """Stable identifier for a SODA row — uses borough+block+lot+month."""
    parts = [
        row.get("borough", ""),
        row.get("block", ""),
        row.get("lot", ""),
        row.get("month", ""),
        row.get("lien_cycle", ""),
    ]
    return "|".join(str(p).strip() for p in parts)


def fetch_lien_rows(borough_digit: str, block: str, lot: str) -> list[dict[str, Any]]:
    """
    Fetch all lien records from SODA for a given borough/block/lot.

    The DOF dataset uses a numeric borough code (1–5) in its 'borough' field,
    not a name.  Block and lot are stored as plain integers (no zero-padding).
    """
    try:
        block_int = str(int(block))
        lot_int = str(int(lot))
    except (ValueError, TypeError):
        return []

    params: dict[str, Any] = {
        "$where": (
            f"borough='{borough_digit}' "
            f"AND block='{block_int}' "
            f"AND lot='{lot_int}'"
        ),
        "$limit": PAGE_SIZE,
        "$order": "month DESC",
    }
    try:
        resp = requests.get(SODA_BASE, params=params, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:  # noqa: BLE001
        print(f"  [WARN] SODA fetch failed for {borough_digit}/{block_int}/{lot_int}: {exc}")
        return []


def parse_lien_row(
    row: dict[str, Any],
    property_id: str,
    bbl: str,
) -> dict[str, Any]:
    """Convert a raw SODA row into a lien_history insert dict."""
    water_raw = str(row.get("water_debt_only", "")).strip().upper()
    water_only = water_raw in ("Y", "YES", "TRUE", "1")

    amount_raw = row.get("lien_amount") or row.get("amount_of_lien")
    try:
        lien_amount: float | None = float(amount_raw) if amount_raw is not None else None
    except (ValueError, TypeError):
        lien_amount = None

    # notice_month: normalize to YYYY-MM-DD (DOF field is often YYYY-MM-DDT00:00:00.000)
    month_raw = str(row.get("month", "")).strip()
    notice_month = month_raw[:10] if month_raw else None

    return {
        "property_id": property_id,
        "bbl": bbl,
        "tax_class": row.get("tax_class_code") or row.get("tax_class"),
        "building_class": row.get("building_class"),
        "lien_cycle": row.get("lien_cycle"),
        "water_debt_only": water_only,
        "lien_amount": lien_amount,
        "notice_month": notice_month,
        "source_row_id": _source_row_id(row),
    }


def upsert_lien_records(records: list[dict[str, Any]], client: Any) -> int:
    """Upsert records into lien_history. Returns number of rows written."""
    if not records:
        return 0
    client.table("lien_history").upsert(
        records,
        on_conflict="property_id,source_row_id",
    ).execute()
    return len(records)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest prior lien history for all tracked properties")
    parser.add_argument("--limit", type=int, default=0, help="Cap on number of properties to process (0 = all)")
    parser.add_argument("--dry-run", action="store_true", help="Parse without writing to Supabase")
    args = parser.parse_args()

    client = get_client()

    # Load all properties that have block + lot + borough
    result = (
        client.table("properties")
        .select("id, address, borough, block, lot")
        .not_.is_("block", "null")
        .not_.is_("lot", "null")
        .execute()
    )
    properties: list[dict[str, Any]] = result.data or []

    if args.limit:
        properties = properties[: args.limit]

    total = len(properties)
    print(f"Processing {total} properties with block/lot data…")

    total_written = 0
    skipped = 0

    for i, prop in enumerate(properties, 1):
        pid = prop["id"]
        borough = prop.get("borough", "")
        block = prop.get("block", "")
        lot = prop.get("lot", "")
        address = prop.get("address", "?")

        borough_digit = BOROUGH_DIGIT.get(borough)
        if not borough_digit:
            print(f"  [{i}/{total}] SKIP {address} — unknown borough '{borough}'")
            skipped += 1
            continue

        bbl = _build_bbl(borough, block, lot)
        if not bbl:
            print(f"  [{i}/{total}] SKIP {address} — cannot build BBL (block={block}, lot={lot})")
            skipped += 1
            continue

        rows = fetch_lien_rows(borough_digit, block, lot)
        if not rows:
            print(f"  [{i}/{total}] {address} — 0 lien records")
            time.sleep(REQUEST_DELAY)
            continue

        records = [parse_lien_row(r, pid, bbl) for r in rows]

        if args.dry_run:
            print(f"  [{i}/{total}] DRY-RUN {address} — would upsert {len(records)} lien record(s)")
        else:
            written = upsert_lien_records(records, client)
            total_written += written
            print(f"  [{i}/{total}] {address} — upserted {written} lien record(s)")

        time.sleep(REQUEST_DELAY)

    print(f"\nDone. Properties processed: {total - skipped}/{total}  |  Records written: {total_written}  |  Skipped: {skipped}")


if __name__ == "__main__":
    main()
