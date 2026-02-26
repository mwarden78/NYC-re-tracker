"""Backfill BBL (and lat/lng) for listings that were ingested without geocoding.

The borough sweep (Brooklyn, Bronx, Staten Island, Queens) was run with
--skip-geocode so those ~6,658 listings have no BBL.  This script geocodes
them via the NYC Planning GeoSearch API and writes bbl, latitude, and
longitude back to the listings row.

GeoSearch is a free, unauthenticated API — rate-limit to 1 req/s.

Usage:
  python data/backfill_listings_bbl.py               # all un-BBL'd listings
  python data/backfill_listings_bbl.py --limit 100   # cap at 100 rows
  python data/backfill_listings_bbl.py --dry-run     # preview, no writes
  python data/backfill_listings_bbl.py --borough BK  # one borough only
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from typing import Optional

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from data.ingest_nyc_open_data import geocode_address  # noqa: E402
from utils.supabase_client import get_client, fetch_all_rows  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

BOROUGH_ABBREV: dict[str, str] = {
    "MN": "Manhattan",
    "BK": "Brooklyn",
    "BX": "Bronx",
    "QN": "Queens",
    "SI": "Staten Island",
}

GEOSEARCH_RATE_LIMIT = 1.0  # seconds between requests


def backfill(
    limit: Optional[int] = None,
    dry_run: bool = False,
    borough_filter: Optional[str] = None,
) -> None:
    client = get_client()

    borough_name: Optional[str] = None
    if borough_filter:
        borough_name = BOROUGH_ABBREV.get(borough_filter.upper())
        if not borough_name:
            log.error("Unknown borough: %s. Use MN, BK, BX, QN, or SI.", borough_filter)
            sys.exit(1)
        log.info("Borough filter: %s", borough_name)

    # Build query for listings with no BBL
    def _query():
        q = client.table("listings").select("id,address,borough,latitude,longitude").is_("bbl", "null")
        if borough_name:
            q = q.eq("borough", borough_name)
        return q

    if limit:
        rows = _query().limit(limit).execute().data or []
    else:
        rows = fetch_all_rows(_query)

    log.info("Found %d listings with null BBL%s", len(rows),
             f" in {borough_name}" if borough_name else "")

    if not rows:
        log.info("Nothing to do.")
        return

    updated = 0
    no_bbl = 0
    skipped = 0

    for i, row in enumerate(rows):
        address = (row.get("address") or "").strip()
        borough = (row.get("borough") or "").strip()

        if not address or not borough:
            log.debug("[%d/%d] Skipping — missing address or borough", i + 1, len(rows))
            skipped += 1
            continue

        lat, lng, bbl = geocode_address(address, borough)

        if bbl:
            fields: dict = {"bbl": bbl}
            # Prefer GeoSearch coordinates over RentCast's (more precise for NYC)
            if lat is not None and lng is not None:
                fields["latitude"] = lat
                fields["longitude"] = lng
            log.info("[%d/%d] %s, %s -> BBL %s", i + 1, len(rows), address, borough, bbl)
            if not dry_run:
                client.table("listings").update(fields).eq("id", row["id"]).execute()
            updated += 1
        else:
            log.info("[%d/%d] %s, %s -> no BBL found", i + 1, len(rows), address, borough)
            no_bbl += 1

        if (i + 1) % 500 == 0:
            log.info("Progress: %d/%d processed (%d updated, %d no BBL, %d skipped)",
                     i + 1, len(rows), updated, no_bbl, skipped)

        time.sleep(GEOSEARCH_RATE_LIMIT)

    hit_rate = updated / (updated + no_bbl) * 100 if (updated + no_bbl) > 0 else 0
    if dry_run:
        log.info("[DRY RUN] Would update %d/%d listings (%.1f%% hit rate, %d no BBL, %d skipped)",
                 updated, len(rows), hit_rate, no_bbl, skipped)
    else:
        log.info("Done. Updated %d/%d listings with BBL (%.1f%% hit rate, %d no BBL, %d skipped).",
                 updated, len(rows), hit_rate, no_bbl, skipped)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Backfill BBL and lat/lng for listings missing geocoding."
    )
    parser.add_argument(
        "--limit", type=int, default=None, metavar="N",
        help="Max number of listings to process (default: all)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Preview without writing to the database",
    )
    parser.add_argument(
        "--borough", metavar="ABBREV", default=None,
        help="Process one borough only: MN, BK, BX, QN, SI",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    backfill(limit=args.limit, dry_run=args.dry_run, borough_filter=args.borough)
