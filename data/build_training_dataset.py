"""Build raw AVM training dataset by joining DOF sales with PLUTO — TES-69.

Joins two local Parquet files on BBL using DuckDB (fast, no server needed):
  - data/training/dof_sales.parquet  — NYC property sales (from TES-67)
  - data/training/pluto.parquet      — NYC lot attributes (from TES-68)

Output: data/training/raw_features.parquet

DuckDB is used instead of pandas for performance: joining 400k+ sales rows
against 870k PLUTO rows via pandas merge would require ~4GB RAM and take
several minutes. DuckDB runs the same join in seconds with minimal memory.

Prerequisites:
  python data/ingest_dof_sales.py   (produces dof_sales.parquet)
  python data/download_pluto.py     (produces pluto.parquet)

Usage:
  python data/build_training_dataset.py            # full join
  python data/build_training_dataset.py --dry-run  # show stats, no file written
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import duckdb

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

DATA_DIR = Path(__file__).parent / "training"
DOF_PATH = DATA_DIR / "dof_sales.parquet"
PLUTO_PATH = DATA_DIR / "pluto.parquet"
OUTPUT_PATH = DATA_DIR / "raw_features.parquet"


# ---------------------------------------------------------------------------
# Join
# ---------------------------------------------------------------------------

JOIN_SQL = """
SELECT
    -- Sale identifiers
    s.bbl,
    s.sale_date,
    s.sale_year,
    s.sale_quarter,
    s.sale_price,

    -- Property basics from DOF
    s.address                             AS sale_address,
    s.borough_name,
    s.zip_code,
    s.neighborhood,
    s.building_class_category,
    s.gross_square_feet,
    s.land_square_feet                    AS sale_land_sqft,
    s.residential_units                   AS sale_res_units,
    s.commercial_units                    AS sale_com_units,
    s.total_units                         AS sale_total_units,
    s.year_built                          AS sale_year_built,

    -- Lot attributes from PLUTO (current snapshot)
    p.latitude,
    p.longitude,
    p.bldgclass,
    p.landuse,
    p.zonedist1,
    p.overlay1,
    p.residfar,
    p.commfar,
    p.facilfar,
    p.builtfar,
    p.lotarea,
    p.bldgarea,
    p.comarea,
    p.resarea,
    p.officearea,
    p.retailarea,
    p.numfloors,
    p.unitsres                            AS pluto_unitsres,
    p.unitstotal                          AS pluto_unitstotal,
    p.yearbuilt                           AS pluto_yearbuilt,
    p.yearalter1,
    p.assessland,
    p.assesstot,
    p.exempttot

FROM read_parquet('{dof_path}') s
LEFT JOIN read_parquet('{pluto_path}') p
    ON s.bbl = p.bbl
WHERE s.sale_price > 10000
"""

STATS_SQL = """
SELECT
    COUNT(*)                                                    AS total_rows,
    COUNT(CASE WHEN p_lat IS NOT NULL THEN 1 END)               AS pluto_matched,
    COUNT(CASE WHEN p_lat IS NULL THEN 1 END)                   AS pluto_unmatched,
    ROUND(COUNT(CASE WHEN p_lat IS NOT NULL THEN 1 END) * 100.0
          / COUNT(*), 1)                                        AS match_pct,
    MIN(sale_date)::DATE                                        AS earliest_sale,
    MAX(sale_date)::DATE                                        AS latest_sale,
    ROUND(AVG(sale_price))::BIGINT                              AS avg_sale_price,
    MEDIAN(sale_price)::BIGINT                                  AS median_sale_price
FROM (
    SELECT s.sale_date, s.sale_price, p.latitude AS p_lat
    FROM read_parquet('{dof_path}') s
    LEFT JOIN read_parquet('{pluto_path}') p ON s.bbl = p.bbl
    WHERE s.sale_price > 10000
)
"""


def check_inputs() -> bool:
    """Verify both input Parquet files exist."""
    ok = True
    for path in (DOF_PATH, PLUTO_PATH):
        if not path.exists():
            log.error("Missing input file: %s", path)
            log.error("  Run: python data/%s", {
                DOF_PATH: "ingest_dof_sales.py",
                PLUTO_PATH: "download_pluto.py",
            }[path])
            ok = False
    return ok


def build(dry_run: bool = False) -> None:
    con = duckdb.connect()

    # --- Stats first (cheap, same query engine) ---
    log.info("Computing join statistics …")
    stats_sql = STATS_SQL.format(dof_path=DOF_PATH, pluto_path=PLUTO_PATH)
    stats = con.execute(stats_sql).fetchone()

    (total, matched, unmatched, match_pct,
     earliest, latest, avg_price, median_price) = stats

    log.info("  Total sale rows (price > $10k): %s", f"{total:,}")
    log.info("  PLUTO matched:   %s (%.1f%%)", f"{matched:,}", match_pct)
    log.info("  PLUTO unmatched: %s — will be kept with NULL PLUTO attrs",
             f"{unmatched:,}")
    log.info("  Sale date range: %s → %s", earliest, latest)
    log.info("  Avg sale price:  $%s", f"{avg_price:,}")
    log.info("  Median sale price: $%s", f"{median_price:,}")

    # --- Borough breakdown ---
    borough_sql = """
        SELECT borough_name, COUNT(*) AS cnt
        FROM read_parquet('{p}')
        WHERE sale_price > 10000
        GROUP BY borough_name
        ORDER BY cnt DESC
    """.format(p=DOF_PATH)
    log.info("  By borough:")
    for row in con.execute(borough_sql).fetchall():
        log.info("    %-15s %s", row[0] or "Unknown", f"{row[1]:,}")

    if dry_run:
        log.info("Dry run — no file written.")
        return

    # --- Full join → Parquet ---
    log.info("Running join and writing output …")
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)

    join_sql = JOIN_SQL.format(dof_path=DOF_PATH, pluto_path=PLUTO_PATH)
    copy_sql = f"COPY ({join_sql}) TO '{OUTPUT_PATH}' (FORMAT PARQUET)"
    con.execute(copy_sql)

    # Verify output
    row_count = con.execute(
        f"SELECT COUNT(*) FROM read_parquet('{OUTPUT_PATH}')"
    ).fetchone()[0]
    size_mb = OUTPUT_PATH.stat().st_size / 1_048_576
    log.info("Wrote %s rows to %s (%.1f MB)", f"{row_count:,}", OUTPUT_PATH, size_mb)

    # Sample
    sample_sql = f"""
        SELECT sale_date::DATE, sale_address, borough_name,
               sale_price, gross_square_feet, bldgclass, zonedist1,
               residfar, builtfar, latitude, longitude
        FROM read_parquet('{OUTPUT_PATH}')
        WHERE latitude IS NOT NULL
        LIMIT 5
    """
    log.info("Sample rows (PLUTO-matched):")
    for row in con.execute(sample_sql).fetchall():
        log.info("  %s", row)

    log.info("Done.")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Join DOF sales with PLUTO attributes to build raw AVM training dataset"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print join statistics without writing output Parquet"
    )
    args = parser.parse_args()

    log.info("=== Build raw AVM training dataset ===")
    log.info("DOF sales:  %s", DOF_PATH)
    log.info("PLUTO:      %s", PLUTO_PATH)
    log.info("Output:     %s", OUTPUT_PATH)

    if not check_inputs():
        sys.exit(1)

    build(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
