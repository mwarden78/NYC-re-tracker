"""311 complaints ingestion script — TES-59.

Fetches NYC 311 service requests for each property by matching
`incident_address` + `borough`, filtering to complaint types relevant
to real estate condition, and upserts into the `complaints_311` table.

Data source (no API key required):
  NYC 311 Service Requests: dataset erm2-nwe9

Usage:
  python data/ingest_311_complaints.py                   # all properties
  python data/ingest_311_complaints.py --limit 50        # first 50 properties
  python data/ingest_311_complaints.py --property-id UUID
  python data/ingest_311_complaints.py --dry-run         # preview, no writes
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from typing import Optional

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from data.ingest_nyc_open_data import soda_get_all  # noqa: E402
from utils.supabase_client import get_client, fetch_all_rows  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Dataset ID
# ---------------------------------------------------------------------------

DATASET_ID = "erm2-nwe9"

# ---------------------------------------------------------------------------
# Complaint type filter — property-condition relevant only
# ---------------------------------------------------------------------------

RELEVANT_COMPLAINT_TYPES = {
    "HEAT/HOT WATER",
    "PLUMBING",
    "PAINT/PLASTER",
    "UNSANITARY CONDITION",
    "RODENT",
    "NOISE - RESIDENTIAL",
    "ILLEGAL CONVERSION",
    "BUILDING CONDITION",
    "STRUCTURAL",
    "ELEVATOR",
    "DOOR/WINDOW",
    "WATER LEAK",
    "FLOORING/STAIRS",
    "GENERAL",
}

# Pre-build a SODA IN(...) clause for the complaint type filter
_TYPES_IN = ", ".join(f"'{t}'" for t in sorted(RELEVANT_COMPLAINT_TYPES))
COMPLAINT_TYPE_FILTER = f"complaint_type in({_TYPES_IN})"


# ---------------------------------------------------------------------------
# Address / borough helpers
# ---------------------------------------------------------------------------

def _normalize_address(address: str) -> str:
    """Uppercase and strip address for SODA match against incident_address."""
    return address.strip().upper()


def _normalize_borough(borough: str) -> str:
    """Normalize borough to match 311 dataset borough values (uppercase)."""
    mapping = {
        "brooklyn":      "BROOKLYN",
        "manhattan":     "MANHATTAN",
        "queens":        "QUEENS",
        "bronx":         "BRONX",
        "staten island": "STATEN ISLAND",
    }
    return mapping.get(borough.lower().strip(), borough.upper().strip())


def _escape_soda(value: str) -> str:
    """Escape single quotes for inclusion in a SODA WHERE string."""
    return value.replace("'", "''")


# ---------------------------------------------------------------------------
# Fetch
# ---------------------------------------------------------------------------

def fetch_complaints(property_id: str, address: str, borough: str) -> list[dict]:
    """Fetch 311 complaints for a single property by address + borough."""
    norm_addr = _normalize_address(address)
    norm_boro = _normalize_borough(borough)

    if not norm_addr or not norm_boro:
        return []

    safe_addr = _escape_soda(norm_addr)
    safe_boro = _escape_soda(norm_boro)

    rows = soda_get_all(
        DATASET_ID,
        where=(
            f"upper(incident_address)='{safe_addr}'"
            f" AND upper(borough)='{safe_boro}'"
            f" AND {COMPLAINT_TYPE_FILTER}"
        ),
        select=(
            "unique_key,complaint_type,descriptor,status,"
            "agency,created_date,closed_date"
        ),
    )

    complaints = []
    for r in rows:
        complaints.append({
            "property_id":    property_id,
            "external_id":    r.get("unique_key"),
            "complaint_type": (r.get("complaint_type") or "").strip() or None,
            "descriptor":     (r.get("descriptor") or "").strip() or None,
            "status":         (r.get("status") or "").strip() or None,
            "agency":         (r.get("agency") or "").strip() or None,
            "created_date":   (r.get("created_date") or "")[:10] or None,
            "closed_date":    (r.get("closed_date") or "")[:10] or None,
        })
    return complaints


# ---------------------------------------------------------------------------
# Upsert
# ---------------------------------------------------------------------------

def upsert_complaints(complaints: list[dict], dry_run: bool = False) -> int:
    if not complaints:
        return 0

    if dry_run:
        log.info("[DRY RUN] Would upsert %d complaint(s):", len(complaints))
        for c in complaints[:5]:
            log.info("  %s", c)
        if len(complaints) > 5:
            log.info("  ... and %d more", len(complaints) - 5)
        return len(complaints)

    client = get_client()
    batch_size = 100
    total = 0
    for i in range(0, len(complaints), batch_size):
        batch = complaints[i: i + batch_size]
        client.table("complaints_311").upsert(
            batch, on_conflict="property_id,external_id"
        ).execute()
        total += len(batch)
    return total


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def load_properties(
    property_id: Optional[str] = None,
    limit: Optional[int] = None,
) -> list[dict]:
    client = get_client()
    query = client.table("properties").select("id,address,borough")
    if property_id:
        query = query.eq("id", property_id)
    if limit:
        return query.limit(limit).execute().data or []
    return fetch_all_rows(query)


def run(
    property_id: Optional[str],
    limit: Optional[int],
    dry_run: bool,
) -> None:
    properties = load_properties(property_id, limit)
    log.info("Processing 311 complaints for %d properties...", len(properties))

    all_complaints: list[dict] = []
    skipped = 0

    for i, prop in enumerate(properties):
        pid     = prop["id"]
        address = prop.get("address", "")
        borough = prop.get("borough", "")
        log.info("  [%d/%d] %s, %s", i + 1, len(properties), address, borough)

        complaints = fetch_complaints(pid, address, borough)
        if complaints:
            log.info("    Found %d complaint(s)", len(complaints))
            all_complaints.extend(complaints)
        else:
            log.info("    No relevant 311 complaints found")
            skipped += 1

        time.sleep(0.2)  # gentle rate limiting between properties

    log.info(
        "Total: %d complaints from %d properties (%d with no matches)",
        len(all_complaints), len(properties), skipped,
    )

    upserted = upsert_complaints(all_complaints, dry_run=dry_run)
    log.info("Upserted %d complaints.", upserted)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Ingest 311 complaints per property into Supabase."
    )
    parser.add_argument(
        "--property-id", metavar="UUID", default=None,
        help="Process a single property by ID",
    )
    parser.add_argument(
        "--limit", type=int, default=None,
        help="Process at most N properties",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Preview results without writing to Supabase",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    run(property_id=args.property_id, limit=args.limit, dry_run=args.dry_run)
