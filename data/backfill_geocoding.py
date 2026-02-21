"""Backfill lat/lng for properties that have null coordinates.

Uses the NYC Planning GeoSearch API (same as the ingestion script).

Usage:
  python data/backfill_geocoding.py            # geocode all nulls
  python data/backfill_geocoding.py --limit 50 # cap at 50 rows
  python data/backfill_geocoding.py --dry-run  # preview without writing
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from typing import Optional

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from data.ingest_nyc_open_data import geocode_address  # noqa: E402
from utils.supabase_client import get_client  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


def backfill(limit: Optional[int] = None, dry_run: bool = False) -> None:
    client = get_client()

    query = client.table("properties").select("id,address,borough").is_("lat", "null")
    if limit:
        query = query.limit(limit)
    rows = query.execute().data

    log.info("Found %d properties with null lat/lng", len(rows))

    updated = 0
    for i, row in enumerate(rows):
        address = row.get("address", "")
        borough = row.get("borough", "")
        if not address or not borough:
            continue

        lat, lng = geocode_address(address, borough)
        status = f"({lat:.5f}, {lng:.5f})" if lat else "no result"
        log.info("  [%d/%d] %s, %s -> %s", i + 1, len(rows), address, borough, status)

        if lat and not dry_run:
            client.table("properties").update({"lat": lat, "lng": lng}).eq("id", row["id"]).execute()
            updated += 1

    if dry_run:
        log.info("[DRY RUN] Would have updated %d properties", sum(1 for r in rows if r))
    else:
        log.info("Done. Updated %d/%d properties with coordinates.", updated, len(rows))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backfill geocoding for properties with null lat/lng.")
    parser.add_argument("--limit", type=int, default=None, metavar="N")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    backfill(limit=args.limit, dry_run=args.dry_run)
