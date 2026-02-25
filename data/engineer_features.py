"""Feature engineering pipeline for AVM training data — TES-70.

Reads data/training/raw_features.parquet (produced by TES-69) and applies:
  1. Outlier / quality filtering
  2. Derived numeric features
  3. Categorical encoding (bldgclass → single letter, zonedist1 → zone prefix)
     — encoders saved to data/models/label_encoders.pkl for reuse at inference
  4. Borough one-hot encoding
  5. Temporal train/test split (is_test column, no shuffling)

Outputs:
  data/training/features_engineered.parquet
  data/models/label_encoders.pkl

Usage:
  python data/engineer_features.py            # run full pipeline
  python data/engineer_features.py --dry-run  # print stats, no files written
"""

from __future__ import annotations

import argparse
import logging
import pickle
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

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
MODELS_DIR = Path(__file__).parent / "models"
INPUT_PATH = DATA_DIR / "raw_features.parquet"
OUTPUT_PATH = DATA_DIR / "features_engineered.parquet"
ENCODERS_PATH = MODELS_DIR / "label_encoders.pkl"

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MIN_SALE_PRICE = 10_000
MAX_SALE_PRICE = 50_000_000
TEST_CUTOFF = pd.Timestamp("2023-01-01")


# ---------------------------------------------------------------------------
# Pipeline steps
# ---------------------------------------------------------------------------

def filter_outliers(df: pd.DataFrame) -> pd.DataFrame:
    """Remove non-arms-length, bulk-transfer, and data-quality outliers."""
    n0 = len(df)

    # Price bounds (raw_features already filters < 10k, but re-apply for safety)
    df = df[(df["sale_price"] >= MIN_SALE_PRICE) & (df["sale_price"] <= MAX_SALE_PRICE)]
    log.info("  After price filter ($%s–$%s): %s rows (-%s)",
             f"{MIN_SALE_PRICE:,}", f"{MAX_SALE_PRICE:,}",
             f"{len(df):,}", f"{n0 - len(df):,}")
    n1 = len(df)

    # Both DOF gross_square_feet and PLUTO bldgarea must be non-zero
    mask = (df["gross_square_feet"].fillna(0) > 0) & (df["bldgarea"].fillna(0) > 0)
    df = df[mask]
    log.info("  After GFA > 0 filter:        %s rows (-%s)",
             f"{len(df):,}", f"{n1 - len(df):,}")
    n2 = len(df)

    # Require PLUTO match (lat/lon present — no PLUTO → can't derive spatial features)
    df = df.dropna(subset=["latitude", "longitude"])
    log.info("  After PLUTO match filter:    %s rows (-%s)",
             f"{len(df):,}", f"{n2 - len(df):,}")

    log.info("  Total filtered: -%s rows (%s kept)",
             f"{n0 - len(df):,}", f"{len(df):,}")
    return df


def derive_features(df: pd.DataFrame) -> pd.DataFrame:
    """Add derived numeric columns."""

    # Price per square foot
    df["price_per_sqft"] = df["sale_price"] / df["gross_square_feet"]

    # Building age at sale: prefer PLUTO yearbuilt, fall back to DOF year_built
    yearbuilt = df["pluto_yearbuilt"].fillna(df["sale_year_built"])
    raw_age = df["sale_year"] - yearbuilt
    # Cap unrealistic values (year_built=0, future dates, etc.)
    df["building_age_at_sale"] = raw_age.clip(lower=0, upper=200)

    # FAR utilization: builtfar / residfar, capped at 1.0
    # Guard against zero/null residfar (e.g. manufacturing zones with no residential FAR)
    safe_residfar = df["residfar"].replace(0, np.nan)
    df["far_utilized_pct"] = (df["builtfar"] / safe_residfar).clip(upper=1.0)

    # Remaining FAR development potential
    df["far_remaining"] = (df["residfar"] - df["builtfar"]).clip(lower=0)

    # Mixed-use: has both residential and commercial units
    df["is_mixed_use"] = (
        (df["sale_com_units"].fillna(0) > 0) &
        (df["sale_res_units"].fillna(0) > 0)
    ).astype(int)

    return df


def _simplify_bldgclass(val: object) -> str:
    """Keep first character of building class (e.g. 'A1' → 'A')."""
    if pd.isna(val) or not str(val).strip():
        return "?"
    return str(val).strip()[0].upper()


def _simplify_zonedist(val: object) -> str:
    """
    Reduce zoning district to letter prefix + first digit group.
    Examples: 'R6B' → 'R6', 'C4-2' → 'C4', 'M1-6' → 'M1', 'R1-2A' → 'R1'.
    """
    if pd.isna(val) or not str(val).strip():
        return "?"
    cleaned = str(val).strip().upper()
    m = re.match(r"^([A-Z]+\d+)", cleaned)
    if m:
        return m.group(1)
    # All-letter codes (e.g. "PARK", "BPC") — return letters only
    letters = re.sub(r"[^A-Z]", "", cleaned)
    return letters or "?"


def encode_categoricals(df: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, dict[str, int]]]:
    """
    Label-encode bldgclass and zonedist1 after simplification.

    Returns the transformed DataFrame and a dict of {col: {label: int_code}}
    suitable for pickling and reuse at inference time.
    """
    encoders: dict[str, dict[str, int]] = {}

    for raw_col, simplify_fn, enc_col in [
        ("bldgclass", _simplify_bldgclass, "bldgclass_enc"),
        ("zonedist1", _simplify_zonedist, "zonedist_enc"),
    ]:
        simplified = df[raw_col].apply(simplify_fn)
        categories = sorted(simplified.unique())
        mapping: dict[str, int] = {cat: i for i, cat in enumerate(categories)}
        df[enc_col] = simplified.map(mapping)
        encoders[raw_col] = mapping
        log.info("  %-12s → %d categories: %s%s",
                 raw_col, len(mapping),
                 str(list(mapping.keys())[:8]),
                 " …" if len(mapping) > 8 else "")

    return df, encoders


def one_hot_borough(df: pd.DataFrame) -> pd.DataFrame:
    """One-hot encode borough_name into indicator columns."""
    dummies = pd.get_dummies(df["borough_name"], prefix="borough", dtype=int)
    # Normalise column names (spaces → underscores)
    dummies.columns = [c.replace(" ", "_") for c in dummies.columns]
    # Ensure all 5 expected columns exist even if a borough is absent from this slice
    for borough in ["Manhattan", "Brooklyn", "Queens", "Bronx", "Staten_Island"]:
        col = f"borough_{borough}"
        if col not in dummies.columns:
            dummies[col] = 0
    return pd.concat([df, dummies], axis=1)


def add_split(df: pd.DataFrame) -> pd.DataFrame:
    """Add is_test column using a strict temporal cutoff (no shuffling)."""
    df["is_test"] = (df["sale_date"] >= TEST_CUTOFF).astype(int)
    train_n = (df["is_test"] == 0).sum()
    test_n = (df["is_test"] == 1).sum()
    log.info("  Train rows (before %s): %s (%.1f%%)",
             TEST_CUTOFF.date(), f"{train_n:,}", 100 * train_n / len(df))
    log.info("  Test  rows (from   %s): %s (%.1f%%)",
             TEST_CUTOFF.date(), f"{test_n:,}", 100 * test_n / len(df))
    return df


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def engineer(dry_run: bool = False) -> None:
    if not INPUT_PATH.exists():
        log.error("Missing input file: %s", INPUT_PATH)
        log.error("  Run: python data/build_training_dataset.py")
        sys.exit(1)

    log.info("Loading %s …", INPUT_PATH)
    df = pd.read_parquet(INPUT_PATH)
    log.info("  %s rows, %d columns", f"{len(df):,}", len(df.columns))

    log.info("Step 1: Filtering outliers …")
    df = filter_outliers(df)

    log.info("Step 2: Deriving features …")
    df = derive_features(df)

    log.info("Step 3: Encoding categoricals …")
    df, encoders = encode_categoricals(df)

    log.info("Step 4: One-hot encoding borough …")
    df = one_hot_borough(df)

    log.info("Step 5: Adding train/test split …")
    df = add_split(df)

    log.info("Final dataset: %s rows, %d columns", f"{len(df):,}", len(df.columns))

    # Derived-feature summary
    log.info("Derived feature stats:")
    for col in ["price_per_sqft", "building_age_at_sale",
                "far_utilized_pct", "far_remaining", "is_mixed_use"]:
        if col in df.columns:
            s = df[col].dropna()
            log.info("  %-25s  mean=%8.2f  median=%8.2f  null=%d",
                     col, s.mean(), s.median(), df[col].isna().sum())

    if dry_run:
        log.info("Dry run — no files written.")
        return

    # Write engineered features Parquet
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(OUTPUT_PATH, index=False)
    size_mb = OUTPUT_PATH.stat().st_size / 1_048_576
    log.info("Wrote %s rows → %s (%.1f MB)", f"{len(df):,}", OUTPUT_PATH, size_mb)

    # Save label encoders
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    with open(ENCODERS_PATH, "wb") as fh:
        pickle.dump(encoders, fh)
    log.info("Saved label encoders → %s", ENCODERS_PATH)

    log.info("Done.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Feature engineering pipeline for AVM training data"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print statistics without writing output files",
    )
    args = parser.parse_args()

    log.info("=== Feature engineering pipeline ===")
    log.info("Input:    %s", INPUT_PATH)
    log.info("Output:   %s", OUTPUT_PATH)
    log.info("Encoders: %s", ENCODERS_PATH)

    engineer(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
