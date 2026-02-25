"""Download full NYC MapPLUTO dataset to Parquet — TES-68.

Downloads all ~870k tax lot records from the NYC MapPLUTO dataset
(Socrata dataset 64uk-42ks) and saves a subset of columns to a local
Parquet file for use as property attribute lookup during feature
engineering and model scoring.

Output: data/training/pluto.parquet

Usage:
  python data/download_pluto.py            # download full dataset
  python data/download_pluto.py --limit 1000  # download first 1000 rows (testing)
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from typing import Optional

import pandas as pd
import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PLUTO_DATASET_ID = "64uk-42ks"
SODA_BASE = "https://data.cityofnewyork.us/resource"
REQUEST_TIMEOUT = 60
PAGE_SIZE = 50_000  # Socrata max per request

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "training")
OUTPUT_PATH = os.path.join(OUTPUT_DIR, "pluto.parquet")

# Columns to retain (per ticket spec)
KEEP_COLUMNS = [
    "bbl", "borough", "block", "lot", "address", "zipcode",
    "latitude", "longitude",
    "bldgclass", "landuse", "zonedist1", "overlay1",
    "residfar", "commfar", "facilfar", "builtfar",
    "lotarea", "bldgarea", "comarea", "resarea", "officearea", "retailarea",
    "numfloors", "unitsres", "unitstotal",
    "yearbuilt", "yearalter1",
    "assessland", "assesstot", "exempttot",
]


# ---------------------------------------------------------------------------
# Socrata helpers
# ---------------------------------------------------------------------------

def _soda_headers() -> dict[str, str]:
    headers = {"Accept": "application/json"}
    token = os.environ.get("NYC_OPEN_DATA_APP_TOKEN")
    if token:
        headers["X-App-Token"] = token
    return headers


def fetch_page(offset: int, limit: int) -> list[dict]:
    """Fetch one page of PLUTO records from Socrata."""
    url = f"{SODA_BASE}/{PLUTO_DATASET_ID}.json"
    params = {
        "$select": ", ".join(KEEP_COLUMNS),
        "$limit": limit,
        "$offset": offset,
        "$order": ":id",
    }
    for attempt in range(3):
        try:
            resp = requests.get(
                url, params=params, headers=_soda_headers(), timeout=REQUEST_TIMEOUT,
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            if attempt < 2:
                wait = 2 ** (attempt + 1)
                log.warning("Fetch failed (attempt %d): %s — retrying in %ds", attempt + 1, exc, wait)
                time.sleep(wait)
            else:
                raise RuntimeError(f"Failed to fetch PLUTO page at offset {offset} after 3 attempts") from exc
    return []  # unreachable


# ---------------------------------------------------------------------------
# Main download logic
# ---------------------------------------------------------------------------

def download(limit: Optional[int] = None) -> None:
    """Download the full PLUTO dataset and save to Parquet."""
    all_rows: list[dict] = []
    offset = 0
    page_size = min(PAGE_SIZE, limit) if limit else PAGE_SIZE

    log.info("Starting PLUTO download (page size: %d)...", page_size)

    while True:
        log.info("Fetching offset %d ...", offset)
        rows = fetch_page(offset, page_size)
        if not rows:
            break
        all_rows.extend(rows)
        log.info("  fetched %d rows (total so far: %d)", len(rows), len(all_rows))

        if limit and len(all_rows) >= limit:
            all_rows = all_rows[:limit]
            break
        if len(rows) < page_size:
            break

        offset += page_size
        time.sleep(0.3)  # gentle rate limiting

    if not all_rows:
        log.error("No rows fetched from PLUTO. Exiting.")
        sys.exit(1)

    log.info("Total rows fetched: %d", len(all_rows))

    # Build DataFrame and normalize BBL to 10-digit string
    df = pd.DataFrame(all_rows)

    # Normalize BBL: PLUTO returns it as a float string like "3002750022"
    if "bbl" in df.columns:
        df["bbl"] = (
            pd.to_numeric(df["bbl"], errors="coerce")
            .dropna()
            .astype("int64")
            .astype(str)
            .str.zfill(10)
        )
        # Re-assign to handle rows that were NaN
        df["bbl"] = (
            pd.to_numeric(df["bbl"], errors="coerce")
            .apply(lambda x: str(int(x)).zfill(10) if pd.notna(x) else None)
        )

    # Convert numeric columns
    numeric_cols = [
        "residfar", "commfar", "facilfar", "builtfar",
        "lotarea", "bldgarea", "comarea", "resarea", "officearea", "retailarea",
        "numfloors", "unitsres", "unitstotal",
        "yearbuilt", "yearalter1",
        "assessland", "assesstot", "exempttot",
    ]
    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    # Ensure output directory exists
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    df.to_parquet(OUTPUT_PATH, index=False, engine="pyarrow")
    log.info("Saved %d rows to %s", len(df), OUTPUT_PATH)
    log.info("File size: %.1f MB", os.path.getsize(OUTPUT_PATH) / 1_048_576)

    # Quick sanity check
    log.info("Columns: %s", list(df.columns))
    log.info("BBL sample: %s", df["bbl"].dropna().head(3).tolist())


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download full NYC MapPLUTO dataset to Parquet.",
    )
    parser.add_argument(
        "--limit", type=int, default=None, metavar="N",
        help="Max rows to download (default: all ~870k)",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    download(limit=args.limit)
