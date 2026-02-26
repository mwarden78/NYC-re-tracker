"""HPD Building Registration ingestion script — TES-57.

Fetches owner and managing agent contact information from the NYC Housing
Preservation & Development (HPD) building registration database for each
property, matched by house_number + street_name + boro.

Data sources (no API key required):
  HPD Multiple Dwelling Registrations: dataset tesw-yqqr
  HPD Registration Contacts:           dataset feu5-w2e2

Note: The ticket referenced dataset mnqr-ivp3, which is retired. The current
active datasets are tesw-yqqr (building-level) and feu5-w2e2 (contacts).

Usage:
  python data/ingest_hpd_registration.py                  # all properties
  python data/ingest_hpd_registration.py --limit 50       # first 50
  python data/ingest_hpd_registration.py --property-id UUID
  python data/ingest_hpd_registration.py --dry-run        # preview, no writes
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from datetime import date
from typing import Optional

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from data.ingest_nyc_open_data import soda_get_all      # noqa: E402
from utils.supabase_client import get_client, fetch_all_rows  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Dataset IDs
# ---------------------------------------------------------------------------

REGISTRATIONS_DATASET = "tesw-yqqr"  # Multiple Dwelling Registrations (building-level)
CONTACTS_DATASET      = "feu5-w2e2"  # Registration Contacts (owners / agents)

# Owner-type contact roles (used to extract owner name/type)
OWNER_ROLES = {"CorporateOwner", "IndividualOwner", "JointOwner"}

# Preferred agent contact roles (in priority order)
AGENT_ROLES = ["Agent", "SiteManager", "HeadOfficer", "Officer", "Lessee"]


# ---------------------------------------------------------------------------
# Address helpers
# ---------------------------------------------------------------------------

def _soql_escape(s: str) -> str:
    """Escape single quotes for Socrata SoQL string literals ('' is the SQL escape)."""
    return s.replace("'", "''")


def _split_address(address: str) -> tuple[str, str]:
    """Split '123 Atlantic Ave' into ('123', 'ATLANTIC AVE')."""
    parts = address.strip().split(" ", 1)
    house = parts[0].upper() if parts else ""
    street = parts[1].upper() if len(parts) > 1 else ""
    return house, street


def _boro_name(borough: str) -> str:
    """Normalize borough to match HPD boro field values."""
    mapping = {
        "brooklyn":     "BROOKLYN",
        "manhattan":    "MANHATTAN",
        "queens":       "QUEENS",
        "bronx":        "BRONX",
        "staten island": "STATEN ISLAND",
    }
    return mapping.get(borough.lower().strip(), borough.upper().strip())


# ---------------------------------------------------------------------------
# Fetch helpers
# ---------------------------------------------------------------------------

def fetch_registrations(house: str, street: str, boro: str) -> list[dict]:
    """Return all HPD registration rows for a given address."""
    return soda_get_all(
        REGISTRATIONS_DATASET,
        where=(
            f"upper(housenumber)='{_soql_escape(house)}'"
            f" AND upper(streetname)='{_soql_escape(street)}'"
            f" AND upper(boro)='{_soql_escape(boro)}'"
        ),
        select="registrationid,registrationenddate,lastregistrationdate",
    )


def fetch_contacts(registration_id: str) -> list[dict]:
    """Return all contact rows for a given HPD registration ID."""
    return soda_get_all(
        CONTACTS_DATASET,
        where=f"registrationid='{registration_id}'",
        select="type,corporationname,firstname,middleinitial,lastname,contactdescription",
    )


# ---------------------------------------------------------------------------
# Data extraction
# ---------------------------------------------------------------------------

def _derive_lifecycle_stage(registration_end_date_raw: str | None) -> str:
    """Return 'Active' or 'Terminated' based on registrationenddate."""
    if not registration_end_date_raw:
        return "Terminated"
    try:
        end_str = registration_end_date_raw[:10]  # YYYY-MM-DD
        end_date = date.fromisoformat(end_str)
        return "Active" if end_date >= date.today() else "Terminated"
    except Exception:
        return "Terminated"


def _build_name(row: dict) -> str | None:
    """Build a display name from a contact row (corporation or person)."""
    corp = (row.get("corporationname") or "").strip()
    if corp:
        return corp
    first = (row.get("firstname") or "").strip()
    last  = (row.get("lastname") or "").strip()
    mi    = (row.get("middleinitial") or "").strip()
    parts = [p for p in [first, mi, last] if p]
    return " ".join(parts) if parts else None


def _owner_type_label(role: str) -> str:
    """Map HPD contact role to a human-readable owner type."""
    if role == "CorporateOwner":
        return "Corporation"
    if role == "JointOwner":
        return "Joint"
    return "Individual"


def extract_registration_record(
    property_id: str,
    reg: dict,
    contacts: list[dict],
) -> dict:
    """Build a single hpd_registrations row from a registration + its contacts."""
    reg_id = str(reg.get("registrationid", ""))
    end_raw = reg.get("registrationenddate")
    lifecycle = _derive_lifecycle_stage(end_raw)
    end_date = end_raw[:10] if end_raw else None

    # Pick owner (first matching owner-role contact)
    owner_name: str | None = None
    owner_type: str | None = None
    for c in contacts:
        role = c.get("type", "")
        if role in OWNER_ROLES:
            owner_name = _build_name(c)
            owner_type = _owner_type_label(role)
            break

    # Pick agent (first matching agent-role contact, in priority order)
    contact_name: str | None = None
    contact_type: str | None = None
    for preferred_role in AGENT_ROLES:
        for c in contacts:
            if c.get("type") == preferred_role:
                contact_name = _build_name(c)
                contact_type = preferred_role
                break
        if contact_name:
            break

    return {
        "property_id":           property_id,
        "registration_id":       reg_id,
        "lifecycle_stage":       lifecycle,
        "owner_name":            owner_name,
        "owner_type":            owner_type,
        "contact_name":          contact_name,
        "contact_type":          contact_type,
        "registration_end_date": end_date,
    }


# ---------------------------------------------------------------------------
# Per-property fetch
# ---------------------------------------------------------------------------

def fetch_hpd_registrations(property_id: str, address: str, borough: str) -> list[dict]:
    """Fetch HPD registration records for a single property."""
    house, street = _split_address(address)
    if not house or not street:
        return []

    boro = _boro_name(borough)
    registrations = fetch_registrations(house, street, boro)

    if not registrations:
        return []

    records = []
    for reg in registrations:
        reg_id = str(reg.get("registrationid", ""))
        if not reg_id:
            continue
        contacts = fetch_contacts(reg_id)
        record = extract_registration_record(property_id, reg, contacts)
        records.append(record)
        time.sleep(0.1)  # gentle rate limiting between contact fetches

    return records


# ---------------------------------------------------------------------------
# Upsert
# ---------------------------------------------------------------------------

def upsert_registrations(records: list[dict], dry_run: bool = False) -> int:
    if not records:
        return 0

    if dry_run:
        log.info("[DRY RUN] Would upsert %d HPD registration record(s):", len(records))
        for r in records[:5]:
            log.info("  %s", r)
        if len(records) > 5:
            log.info("  ... and %d more", len(records) - 5)
        return len(records)

    client = get_client()
    batch_size = 100
    total = 0
    for i in range(0, len(records), batch_size):
        batch = records[i: i + batch_size]
        client.table("hpd_registrations").upsert(
            batch, on_conflict="property_id,registration_id"
        ).execute()
        total += len(batch)
    return total


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def load_properties(property_id: Optional[str] = None, limit: Optional[int] = None) -> list[dict]:
    client = get_client()
    query = client.table("properties").select("id,address,borough")
    if property_id:
        query = query.eq("id", property_id)
    if limit:
        return query.limit(limit).execute().data or []
    return fetch_all_rows(query)


def run(property_id: Optional[str], limit: Optional[int], dry_run: bool) -> None:
    properties = load_properties(property_id, limit)
    log.info("Processing HPD registrations for %d properties...", len(properties))

    all_records: list[dict] = []
    skipped = 0

    for i, prop in enumerate(properties):
        pid     = prop["id"]
        address = prop.get("address", "")
        borough = prop.get("borough", "")
        log.info("  [%d/%d] %s, %s", i + 1, len(properties), address, borough)

        try:
            records = fetch_hpd_registrations(pid, address, borough)
        except Exception as exc:
            log.warning("    ERROR fetching HPD registration, skipping: %s", exc)
            skipped += 1
            continue

        if records:
            log.info("    Found %d registration(s)", len(records))
            for r in records:
                log.info(
                    "      %s | %s | owner=%s (%s) | agent=%s (%s)",
                    r["registration_id"], r["lifecycle_stage"],
                    r["owner_name"], r["owner_type"],
                    r["contact_name"], r["contact_type"],
                )
            all_records.extend(records)
        else:
            log.info("    No HPD registration found")
            skipped += 1

        time.sleep(0.2)  # gentle rate limiting between properties

    log.info(
        "Total: %d records from %d properties (%d with no registration)",
        len(all_records), len(properties), skipped,
    )

    upserted = upsert_registrations(all_records, dry_run=dry_run)
    log.info("Upserted %d HPD registration records.", upserted)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Ingest HPD building registration data into Supabase."
    )
    parser.add_argument("--property-id", metavar="UUID", default=None,
                        help="Process a single property by ID")
    parser.add_argument("--limit", type=int, default=None,
                        help="Process at most N properties")
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview results without writing to Supabase")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    run(property_id=args.property_id, limit=args.limit, dry_run=args.dry_run)
