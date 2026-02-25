"""Walk Score enrichment script — TES-47.

For every property that has lat/lng coordinates, fetches Walk Score,
Transit Score, and Bike Score from the Walk Score Professional API and
writes them back to the `properties` table.

API docs: https://www.walkscore.com/professional/api.php
Requires: WALKSCORE_API_KEY in .env (free tier: 5,000 calls/day)

Usage:
  python data/enrich_walk_score.py               # enrich all properties with coordinates
  python data/enrich_walk_score.py --limit 10    # cap at 10 properties
  python data/enrich_walk_score.py --dry-run     # preview without writing to DB
  python data/enrich_walk_score.py --force       # re-enrich already-scored rows
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from typing import Optional
from urllib.parse import quote

import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from utils.supabase_client import get_client, fetch_all_rows  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

WALKSCORE_API_URL = "https://api.walkscore.com/score"
REQUEST_TIMEOUT = 10
RATE_LIMIT_SLEEP = 1.0  # seconds between requests (free tier safe)

# Walk Score API status codes
WS_STATUS_OK = 1
WS_STATUS_SCORE_UNAVAILABLE = 2
WS_STATUS_DAILY_LIMIT = 40
WS_STATUS_IP_BLOCK = 41
WS_STATUS_INVALID_KEY = 42


def _get_api_key() -> str:
    key = os.environ.get("WALKSCORE_API_KEY", "")
    if not key:
        log.error(
            "WALKSCORE_API_KEY not set. Get a free key at "
            "https://www.walkscore.com/professional/api.php and add it to .env"
        )
        sys.exit(1)
    return key


def fetch_scores(
    address: str, lat: float, lng: float, api_key: str
) -> Optional[dict]:
    """Fetch Walk/Transit/Bike scores for a single address.

    Returns dict with walk_score, transit_score, bike_score (all int or None),
    or None if the request fails or scores are unavailable.
    """
    params = {
        "format": "json",
        "address": address,
        "lat": lat,
        "lon": lng,
        "transit": 1,
        "bike": 1,
        "wsapikey": api_key,
    }
    try:
        resp = requests.get(WALKSCORE_API_URL, params=params, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        log.warning("Walk Score API request failed: %s", exc)
        return None

    status = data.get("status")
    if status == WS_STATUS_DAILY_LIMIT:
        log.error("Walk Score daily API limit reached. Try again tomorrow.")
        sys.exit(1)
    if status == WS_STATUS_INVALID_KEY:
        log.error("Invalid WALKSCORE_API_KEY. Check your .env file.")
        sys.exit(1)
    if status == WS_STATUS_IP_BLOCK:
        log.error("Walk Score API blocked this IP.")
        sys.exit(1)
    if status != WS_STATUS_OK:
        log.debug("Score unavailable for %s (status=%s)", address, status)
        return None

    walk = data.get("walkscore")
    transit = (data.get("transit") or {}).get("score")
    bike = (data.get("bike") or {}).get("score")

    return {
        "walk_score": int(walk) if walk is not None else None,
        "transit_score": int(transit) if transit is not None else None,
        "bike_score": int(bike) if bike is not None else None,
    }


def enrich(
    limit: Optional[int] = None,
    dry_run: bool = False,
    force: bool = False,
) -> None:
    api_key = _get_api_key()
    client = get_client()

    query = (
        client.table("properties")
        .select("id,address,borough,lat,lng")
        .not_.is_("lat", "null")
        .not_.is_("lng", "null")
    )
    if not force:
        # Skip rows that already have all three scores
        query = query.is_("walk_score", "null")
    rows = query.limit(limit).execute().data if limit else fetch_all_rows(query)
    log.info("Found %d properties to enrich with Walk Score data", len(rows))

    if not rows:
        log.info("Nothing to do.")
        return

    enriched = 0
    skipped = 0
    failed = 0

    for i, row in enumerate(rows, 1):
        prop_id = row["id"]
        address = f"{row.get('address', '')}, {row.get('borough', '')}, NY"
        lat = float(row["lat"])
        lng = float(row["lng"])

        log.info("[%d/%d] %s", i, len(rows), row.get("address", prop_id))

        scores = fetch_scores(address, lat, lng, api_key)

        if scores is None:
            log.info("  → No scores available")
            failed += 1
        else:
            log.info(
                "  → Walk: %s  Transit: %s  Bike: %s",
                scores["walk_score"] if scores["walk_score"] is not None else "—",
                scores["transit_score"] if scores["transit_score"] is not None else "—",
                scores["bike_score"] if scores["bike_score"] is not None else "—",
            )
            if not dry_run:
                client.table("properties").update(scores).eq("id", prop_id).execute()
            enriched += 1

        # Rate limit — stay safely under 5,000/day free tier
        if i < len(rows):
            time.sleep(RATE_LIMIT_SLEEP)

    if dry_run:
        log.info(
            "[DRY RUN] Would have enriched %d properties (%d unavailable).",
            enriched, failed,
        )
    else:
        log.info(
            "Done. Enriched %d properties (%d scores unavailable).",
            enriched, failed,
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Enrich properties with Walk/Transit/Bike scores from Walk Score API."
    )
    parser.add_argument("--limit", type=int, default=None, metavar="N",
                        help="Max number of properties to process")
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview without writing to the database")
    parser.add_argument("--force", action="store_true",
                        help="Re-enrich properties that already have walk_score set")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    enrich(limit=args.limit, dry_run=args.dry_run, force=args.force)
