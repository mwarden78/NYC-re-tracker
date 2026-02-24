"""Backfill BBL for existing properties that have a null bbl column.

Uses the NYC Planning GeoSearch API (same as the ingestion script) to look up
the BBL from each property's address + borough.  GeoSearch returns the BBL in
the `addendum.pad.bbl` field of the top feature.

Run this once after applying migration 001_pluto_columns.sql (TES-32) to
populate bbl for records that were ingested before TES-33 started storing it.

Usage:
  python data/backfill_bbl.py               # backfill all null-bbl rows
  python data/backfill_bbl.py --limit 50    # cap at 50 rows
  python data/backfill_bbl.py --dry-run     # preview without writing
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from typing import Optional

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from data.ingest_nyc_open_data import extract_bbl_from_feature, geosearch_feature  # noqa: E402
from utils.supabase_client import get_client  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


def backfill(limit: Optional[int] = None, dry_run: bool = False) -> None:
    client = get_client()

    query = (
        client.table("properties")
        .select("id,address,borough")
        .is_("bbl", "null")
    )
    if limit:
        query = query.limit(limit)
    rows = query.execute().data

    log.info("Found %d properties with null bbl", len(rows))

    updated = 0
    skipped = 0
    for i, row in enumerate(rows):
        address = row.get("address", "")
        borough = row.get("borough", "")
        if not address or not borough:
            skipped += 1
            continue

        feature = geosearch_feature(address, borough)
        bbl = extract_bbl_from_feature(feature) if feature else None

        if bbl:
            log.info("  [%d/%d] %s, %s -> BBL %s", i + 1, len(rows), address, borough, bbl)
            if not dry_run:
                client.table("properties").update({"bbl": bbl}).eq("id", row["id"]).execute()
                updated += 1
        else:
            log.info("  [%d/%d] %s, %s -> no BBL found", i + 1, len(rows), address, borough)
            skipped += 1

        # Gentle rate limiting — GeoSearch is a free public API
        time.sleep(0.1)

    if dry_run:
        log.info("[DRY RUN] Would have updated %d properties (skipped %d)", updated + (len(rows) - skipped - updated), skipped)
    else:
        log.info("Done. Updated %d/%d properties with BBL (skipped %d).", updated, len(rows), skipped)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Backfill BBL for properties with null bbl column."
    )
    parser.add_argument("--limit", type=int, default=None, metavar="N",
                        help="Max number of rows to process")
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview without writing to database")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    backfill(limit=args.limit, dry_run=args.dry_run)
