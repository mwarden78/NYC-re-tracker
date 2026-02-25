"""Backfill lat/lng and BBL for properties that have null coordinates or no BBL.

Uses the NYC Planning GeoSearch API (same as the ingestion script).
The GeoSearch API returns lat, lng, and BBL (Borough-Block-Lot) in a single
call, so this script handles both coordinate and BBL backfills together.

Usage:
  python data/backfill_geocoding.py            # fill all missing lat/lng or BBL
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
from utils.supabase_client import get_client, fetch_all_rows  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


def backfill(limit: Optional[int] = None, dry_run: bool = False) -> None:
    client = get_client()

    # Fetch properties missing lat/lng OR BBL — one GeoSearch call fills both
    query = (
        client.table("properties")
        .select("id,address,borough,lat,bbl")
        .or_("lat.is.null,bbl.is.null")
    )
    rows = query.limit(limit).execute().data if limit else fetch_all_rows(query)

    log.info("Found %d properties missing lat/lng or BBL", len(rows))

    updated = 0
    for i, row in enumerate(rows):
        address = row.get("address", "")
        borough = row.get("borough", "")
        if not address or not borough:
            continue

        lat, lng, bbl = geocode_address(address, borough)

        update_fields: dict = {}
        if row.get("lat") is None and lat is not None:
            update_fields["lat"] = lat
            update_fields["lng"] = lng
        if row.get("bbl") is None and bbl is not None:
            update_fields["bbl"] = bbl

        lat_str = f"{lat:.5f}" if lat else "—"
        log.info(
            "  [%d/%d] %s, %s  lat=%s  bbl=%s",
            i + 1, len(rows), address, borough, lat_str, bbl or "—",
        )

        if update_fields and not dry_run:
            client.table("properties").update(update_fields).eq("id", row["id"]).execute()
            updated += 1

    if dry_run:
        log.info("[DRY RUN] Would have updated %d properties", updated)
    else:
        log.info("Done. Updated %d/%d properties.", updated, len(rows))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Backfill lat/lng and BBL for properties missing either field."
    )
    parser.add_argument("--limit", type=int, default=None, metavar="N")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    backfill(limit=args.limit, dry_run=args.dry_run)
