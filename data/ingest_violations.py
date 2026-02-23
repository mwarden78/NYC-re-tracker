"""HPD and DOB violations ingestion script — TES-17.

Fetches Housing Preservation & Development (HPD) and Department of Buildings
(DOB) violations from NYC Open Data for properties in the Supabase properties
table, then upserts them into the violations table.

Data sources (no API key required):
  HPD Housing Maintenance Code Violations: dataset wvxf-dwi5
  DOB Violations:                          dataset 3h2n-5cm9

Usage:
  python data/ingest_violations.py                   # all properties
  python data/ingest_violations.py --property-id UUID
  python data/ingest_violations.py --source hpd      # HPD only
  python data/ingest_violations.py --source dob      # DOB only
  python data/ingest_violations.py --dry-run         # preview, no writes
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
from utils.supabase_client import get_client         # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Dataset IDs
# ---------------------------------------------------------------------------

HPD_DATASET_ID = "wvxf-dwi5"
DOB_DATASET_ID = "3h2n-5cm9"


# ---------------------------------------------------------------------------
# Address helpers
# ---------------------------------------------------------------------------

def _split_address(address: str) -> tuple[str, str]:
    """Split '123 Atlantic Ave' into ('123', 'ATLANTIC AVE')."""
    parts = address.strip().split(" ", 1)
    house = parts[0].upper() if parts else ""
    street = parts[1].upper() if len(parts) > 1 else ""
    return house, street


def _parse_dob_date(raw: str) -> Optional[str]:
    """Convert DOB date string YYYYMMDD to ISO date YYYY-MM-DD, or None."""
    raw = (raw or "").strip()
    if len(raw) == 8 and raw.isdigit():
        return f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}"
    return None


# ---------------------------------------------------------------------------
# HPD violations
# ---------------------------------------------------------------------------

def fetch_hpd_violations(property_id: str, address: str, borough: str) -> list[dict]:
    """Fetch open and closed HPD violations for a single property."""
    house, street = _split_address(address)
    if not house or not street:
        return []

    rows = soda_get_all(
        HPD_DATASET_ID,
        where=f"upper(housenumber)='{house}' AND upper(streetname)='{street}'",
        select="violationid,class,novdescription,novissueddate,currentstatus,currentstatusdate,violationstatus",
    )

    violations = []
    for r in rows:
        violations.append({
            "property_id": property_id,
            "source": "hpd",
            "external_id": r.get("violationid"),
            "violation_type": r.get("class"),          # A / B / C
            "description": (r.get("novdescription") or "").strip(),
            "status": r.get("violationstatus"),         # "Open" / "Close"
            "issued_date": (r.get("novissueddate") or "")[:10] or None,
            "closed_date": (r.get("currentstatusdate") or "")[:10] if r.get("violationstatus") == "Close" else None,
        })
    return violations


# ---------------------------------------------------------------------------
# DOB violations
# ---------------------------------------------------------------------------

def fetch_dob_violations(property_id: str, address: str) -> list[dict]:
    """Fetch DOB violations for a single property."""
    house, street = _split_address(address)
    if not house or not street:
        return []

    rows = soda_get_all(
        DOB_DATASET_ID,
        where=f"upper(house_number)='{house}' AND upper(street)='{street}'",
        select="isn_dob_bis_viol,violation_type,description,issue_date,violation_category,disposition_comments",
    )

    violations = []
    for r in rows:
        category = r.get("violation_category", "")
        # Derive open/closed from category string
        status = "Open" if "ACTIVE" in category.upper() else "Closed"
        vtype = (r.get("violation_type") or "").split("-", 1)[-1].strip()[:100]

        violations.append({
            "property_id": property_id,
            "source": "dob",
            "external_id": r.get("isn_dob_bis_viol"),
            "violation_type": vtype or None,
            "description": (r.get("description") or "").strip() or None,
            "status": status,
            "issued_date": _parse_dob_date(r.get("issue_date")),
            "closed_date": None,  # DOB dataset doesn't surface a close date
        })
    return violations


# ---------------------------------------------------------------------------
# Upsert
# ---------------------------------------------------------------------------

def upsert_violations(violations: list[dict], dry_run: bool = False) -> int:
    if not violations:
        return 0

    if dry_run:
        log.info("[DRY RUN] Would upsert %d violations:", len(violations))
        for v in violations[:5]:
            log.info("  %s", v)
        if len(violations) > 5:
            log.info("  ... and %d more", len(violations) - 5)
        return len(violations)

    client = get_client()
    batch_size = 100
    total = 0
    for i in range(0, len(violations), batch_size):
        batch = violations[i: i + batch_size]
        client.table("violations").upsert(batch, on_conflict="source,external_id").execute()
        total += len(batch)
    return total


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def load_properties(property_id: Optional[str] = None) -> list[dict]:
    client = get_client()
    query = client.table("properties").select("id,address,borough")
    if property_id:
        query = query.eq("id", property_id)
    return query.execute().data


def run(source: str, property_id: Optional[str], dry_run: bool) -> None:
    properties = load_properties(property_id)
    log.info("Processing violations for %d properties...", len(properties))

    all_violations: list[dict] = []

    for i, prop in enumerate(properties):
        pid = prop["id"]
        address = prop.get("address", "")
        borough = prop.get("borough", "")
        log.info("  [%d/%d] %s, %s", i + 1, len(properties), address, borough)

        if source in ("hpd", "all"):
            hpd = fetch_hpd_violations(pid, address, borough)
            log.info("    HPD: %d violations", len(hpd))
            all_violations.extend(hpd)

        if source in ("dob", "all"):
            dob = fetch_dob_violations(pid, address)
            log.info("    DOB: %d violations", len(dob))
            all_violations.extend(dob)

        time.sleep(0.2)  # gentle rate limiting between properties

    log.info("Total violations fetched: %d", len(all_violations))
    upserted = upsert_violations(all_violations, dry_run=dry_run)
    log.info("Upserted %d violations.", upserted)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Ingest HPD/DOB violations into Supabase.")
    parser.add_argument("--source", choices=["hpd", "dob", "all"], default="all")
    parser.add_argument("--property-id", metavar="UUID", default=None)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    run(source=args.source, property_id=args.property_id, dry_run=args.dry_run)
