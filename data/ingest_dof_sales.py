"""NYC DOF Annualized Sales ingestion script — TES-67.

Downloads every NYC property sale from 2003 to present from the NYC
Citywide Annualized Calendar Sales dataset on NYC Open Data and saves
the result as a Parquet file for AVM model training.

Dataset: NYC Citywide Annualized Calendar Sales Update
Socrata ID: w2pb-icbu
URL: https://data.cityofnewyork.us/City-Government/NYC-Citywide-Annualized-Calendar-Sales-Update/w2pb-icbu

This is training data only — it is never written to Supabase.
Output lives in data/training/dof_sales.parquet (gitignored).

Usage:
  python data/ingest_dof_sales.py                    # full ingest (2003–present)
  python data/ingest_dof_sales.py --limit 5000       # cap rows (for testing)
  python data/ingest_dof_sales.py --year-from 2020   # only fetch from a given year
  python data/ingest_dof_sales.py --dry-run           # print sample rows, no write
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from pathlib import Path

import pandas as pd
import requests

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
DOF_SALES_ID = "w2pb-icbu"
PAGE_SIZE = 50_000          # Socrata max with app token; falls back gracefully without
REQUEST_TIMEOUT = 60        # seconds — large pages are slow
MIN_SALE_PRICE = 10_000     # filter out non-arms-length / nominal transfers

OUTPUT_DIR = Path(__file__).parent / "training"
OUTPUT_PATH = OUTPUT_DIR / "dof_sales.parquet"

# Borough code → name mapping (DOF uses numeric codes 1–5)
BOROUGH_MAP = {
    "1": "Manhattan",
    "2": "Bronx",
    "3": "Brooklyn",
    "4": "Queens",
    "5": "Staten Island",
}

# Fields to keep from the raw Socrata response
KEEP_FIELDS = [
    "borough",
    "neighborhood",
    "building_class_category",
    "block",
    "lot",
    "address",
    "zip_code",
    "residential_units",
    "commercial_units",
    "total_units",
    "land_square_feet",
    "gross_square_feet",
    "year_built",
    "sale_price",
    "sale_date",
]


# ---------------------------------------------------------------------------
# Socrata helpers (consistent with other data/ scripts)
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
            resp = requests.get(
                url, params=params, headers=_soda_headers(), timeout=REQUEST_TIMEOUT
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            if attempt < 2:
                wait = 2 ** attempt
                log.warning(
                    "DOF fetch failed (attempt %d): %s — retrying in %ds",
                    attempt + 1, exc, wait,
                )
                time.sleep(wait)
            else:
                log.error("DOF fetch failed after 3 attempts: %s", exc)
                raise
    return []


# ---------------------------------------------------------------------------
# BBL derivation
# ---------------------------------------------------------------------------

def _derive_bbl(borough: str, block: str, lot: str) -> str | None:
    """Build a 10-digit BBL from DOF borough/block/lot fields.

    DOF stores borough as '1'–'5', block and lot as bare numerics
    (no leading zeros). BBL format: B(1) + BLOCK(5, zero-padded) + LOT(4, zero-padded).
    """
    try:
        b = str(int(borough)).strip()
        bl = str(int(block)).zfill(5)
        lo = str(int(lot)).zfill(4)
        return f"{b}{bl}{lo}"
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Fetch
# ---------------------------------------------------------------------------

def fetch_all_sales(year_from: int = 2003, limit: int | None = None) -> list[dict]:
    """Paginate through the full DOF sales dataset and return all rows."""
    rows: list[dict] = []
    offset = 0
    page_num = 0

    where_clause = f"sale_date >= '{year_from}-01-01T00:00:00.000'"

    while True:
        effective_limit = PAGE_SIZE
        if limit is not None:
            remaining = limit - len(rows)
            if remaining <= 0:
                break
            effective_limit = min(PAGE_SIZE, remaining)

        params = {
            "$where": where_clause,
            "$limit": effective_limit,
            "$offset": offset,
            "$order": "sale_date ASC",
        }

        page_num += 1
        log.info("Fetching page %d (offset=%d, limit=%d) …", page_num, offset, effective_limit)

        page = _soda_get(DOF_SALES_ID, params)
        if not page:
            log.info("Empty page — fetch complete.")
            break

        rows.extend(page)
        log.info("  → %d rows this page | %d total so far", len(page), len(rows))

        if len(page) < effective_limit:
            log.info("Partial page — reached end of dataset.")
            break

        offset += len(page)
        time.sleep(0.1)  # courtesy pause between pages

    return rows


# ---------------------------------------------------------------------------
# Clean + transform
# ---------------------------------------------------------------------------

def clean(rows: list[dict]) -> pd.DataFrame:
    """Convert raw Socrata rows to a clean DataFrame ready for Parquet."""
    df = pd.DataFrame(rows)

    # Normalise column names (Socrata sometimes returns with spaces or mixed case)
    df.columns = [c.strip().lower().replace(" ", "_") for c in df.columns]

    # Keep only fields we need (drop any that are missing from this dataset version)
    available = [f for f in KEEP_FIELDS if f in df.columns]
    missing = [f for f in KEEP_FIELDS if f not in df.columns]
    if missing:
        log.warning("Fields not found in dataset, skipping: %s", missing)
    df = df[available].copy()

    # Numeric coercions
    for col in ("sale_price", "residential_units", "commercial_units", "total_units",
                "gross_square_feet", "land_square_feet", "year_built", "block", "lot"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    # Parse sale_date
    if "sale_date" in df.columns:
        df["sale_date"] = pd.to_datetime(df["sale_date"], errors="coerce")
        df["sale_year"] = df["sale_date"].dt.year.astype("Int16")
        df["sale_quarter"] = df["sale_date"].dt.quarter.astype("Int8")

    # Derive BBL
    if all(c in df.columns for c in ("borough", "block", "lot")):
        df["bbl"] = df.apply(
            lambda r: _derive_bbl(str(r["borough"]), str(r["block"]), str(r["lot"])),
            axis=1,
        )
    else:
        df["bbl"] = None

    # Map borough code → name
    if "borough" in df.columns:
        df["borough_name"] = df["borough"].astype(str).map(BOROUGH_MAP)

    # Filter non-arms-length transfers
    pre_filter = len(df)
    df = df[df["sale_price"] > MIN_SALE_PRICE].copy()
    log.info(
        "Filtered %d rows with sale_price <= $%d — %d rows remain",
        pre_filter - len(df), MIN_SALE_PRICE, len(df),
    )

    # Drop rows with no sale_date or no bbl
    df = df.dropna(subset=["sale_date"])
    df = df[df["bbl"].notna() & (df["bbl"] != "None")]

    return df.reset_index(drop=True)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest NYC DOF Annualized Sales to Parquet")
    parser.add_argument("--limit", type=int, default=None,
                        help="Cap total rows fetched (useful for testing)")
    parser.add_argument("--year-from", type=int, default=2003,
                        help="Only fetch sales from this year onwards (default: 2003)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print sample rows and stats; do not write Parquet file")
    args = parser.parse_args()

    log.info("=== NYC DOF Annualized Sales ingestion ===")
    log.info("Year from: %d | Limit: %s | Dry run: %s",
             args.year_from, args.limit or "none", args.dry_run)

    # Fetch
    rows = fetch_all_sales(year_from=args.year_from, limit=args.limit)
    log.info("Fetched %d raw rows from Socrata", len(rows))

    if not rows:
        log.error("No rows returned — check dataset ID or network connectivity")
        sys.exit(1)

    # Clean
    df = clean(rows)
    log.info("Clean dataset: %d rows", len(df))

    # Stats
    log.info("Year range: %s – %s",
             df["sale_date"].min().date() if not df.empty else "N/A",
             df["sale_date"].max().date() if not df.empty else "N/A")
    log.info("Sale price range: $%s – $%s",
             f"{int(df['sale_price'].min()):,}" if not df.empty else "N/A",
             f"{int(df['sale_price'].max()):,}" if not df.empty else "N/A")
    if "borough_name" in df.columns:
        log.info("By borough:\n%s", df["borough_name"].value_counts().to_string())
    bbl_match_rate = df["bbl"].notna().mean() * 100
    log.info("BBL match rate: %.1f%%", bbl_match_rate)

    if args.dry_run:
        log.info("--- Sample rows (dry run, no file written) ---")
        print(df[["sale_date", "address", "borough_name", "sale_price",
                   "gross_square_feet", "bbl"]].head(10).to_string(index=False))
        return

    # Write Parquet
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    df.to_parquet(OUTPUT_PATH, index=False, engine="pyarrow")
    size_mb = OUTPUT_PATH.stat().st_size / 1_048_576
    log.info("Saved %d rows to %s (%.1f MB)", len(df), OUTPUT_PATH, size_mb)
    log.info("Done.")


if __name__ == "__main__":
    main()
