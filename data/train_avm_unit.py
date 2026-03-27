"""Train unit-level AVM for condo/co-op properties — Option C / TES-118.

Reads data/training/features_engineered_full.parquet (all property types,
produced by engineer_features.py) and filters to unit-sale rows
(gross_square_feet = 0, i.e. individual condo/co-op unit sales from NYC DOF).

Key feature: per_unit_sqft = PLUTO bldgarea / pluto_unitsres
  — building-average unit size, used as training proxy for individual unit size.
  — at inference, RentCast "sqft" (actual unit interior sqft) is used instead.

Artifacts saved to data/models/:
  avm_unit_v1.json               — XGBoost model (native JSON)
  feature_columns_unit_v1.json   — ordered feature list for inference

Prerequisites:
  python data/engineer_features.py   # produces features_engineered_full.parquet

Usage:
  python data/train_avm_unit.py           # full training run
  python data/train_avm_unit.py --dry-run # validate data, no training
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import xgboost as xgb
from sklearn.metrics import r2_score, root_mean_squared_error

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent / "training"
MODELS_DIR = Path(__file__).parent / "models"
INPUT_PATH = DATA_DIR / "features_engineered_full.parquet"   # all rows incl. condos
MODEL_PATH = MODELS_DIR / "avm_unit_v1.json"
FEATURE_COLS_PATH = MODELS_DIR / "feature_columns_unit_v1.json"

# Unit-level features: no gross_square_feet (it's 0 for all training rows).
# per_unit_sqft = PLUTO bldgarea / pluto_unitsres is the size proxy.
# At inference: per_unit_sqft comes from RentCast "sqft" (actual unit size).
UNIT_FEATURES = [
    "per_unit_sqft",         # unit size proxy (PLUTO avg in training; RentCast sqft at inference)
    "numfloors",             # building height (proxy for quality/density tier)
    "pluto_unitsres",        # number of units in building
    "building_age_at_sale",  # building age
    "far_utilized_pct",      # density utilization
    "far_remaining",         # remaining development potential
    "sale_year",             # temporal
    "sale_quarter",          # temporal
    "latitude",              # spatial
    "longitude",             # spatial
    "bldgclass_enc",         # building class (encoded)
    "zonedist_enc",          # zoning district (encoded)
    "borough_Bronx",
    "borough_Brooklyn",
    "borough_Manhattan",
    "borough_Queens",
    "borough_Staten_Island",
]

TARGET = "sale_price"

# Individual condo/co-op unit sales rarely exceed $5M in NYC.
# This cap excludes bulk/portfolio transfers recorded with gross_square_feet=0
# that would otherwise inflate the model's predictions.
MAX_UNIT_SALE_PRICE = 5_000_000

XGB_PARAMS = dict(
    n_estimators=1000,
    learning_rate=0.05,
    max_depth=6,
    subsample=0.8,
    colsample_bytree=0.8,
    eval_metric="mape",
    tree_method="hist",
    random_state=42,
    n_jobs=-1,
)
EARLY_STOPPING_ROUNDS = 50


def mape(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    mask = y_true > 0
    return float(np.mean(np.abs((y_true[mask] - y_pred[mask]) / y_true[mask])) * 100)


def median_ape(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    mask = y_true > 0
    return float(np.median(np.abs((y_true[mask] - y_pred[mask]) / y_true[mask])) * 100)


def _to_numeric(df: pd.DataFrame) -> pd.DataFrame:
    return df.apply(pd.to_numeric, errors="coerce")


def load_and_filter() -> pd.DataFrame:
    """Load full engineered dataset and filter to unit sales only."""
    if not INPUT_PATH.exists():
        log.error("Missing input: %s", INPUT_PATH)
        log.error("  Run: python data/engineer_features.py")
        sys.exit(1)

    df = pd.read_parquet(INPUT_PATH)
    log.info("Loaded %s rows from %s", f"{len(df):,}", INPUT_PATH)

    # Unit sales: gross_square_feet = 0 in NYC DOF (condo/co-op individual unit sales)
    n_all = len(df)
    df = df[df["gross_square_feet"].fillna(0) == 0].copy()
    log.info("Filtered to unit sales (GFA=0): %s rows (%.1f%% of full dataset)",
             f"{len(df):,}", 100 * len(df) / n_all)

    # Exclude bulk/portfolio transfers: cap sale_price at MAX_UNIT_SALE_PRICE.
    # Individual condo/co-op unit sales rarely exceed $5M — higher prices
    # typically reflect whole-floor or building-level transfers recorded as
    # individual unit sales, which would inflate the model's predictions.
    n_before_cap = len(df)
    df = df[df["sale_price"] <= MAX_UNIT_SALE_PRICE].copy()
    log.info("After $%s unit price cap: %s rows (-%s bulk transfers excluded)",
             f"{MAX_UNIT_SALE_PRICE:,}", f"{len(df):,}", f"{n_before_cap - len(df):,}")

    if len(df) == 0:
        log.error("No unit-sale rows found. Check that engineer_features.py "
                  "produced features_engineered_full.parquet correctly.")
        sys.exit(1)

    # Verify per_unit_sqft is meaningful for unit rows
    # (should be PLUTO bldgarea / pluto_unitsres — not 0 for PLUTO-matched rows)
    null_per_unit = df["per_unit_sqft"].isna().sum() if "per_unit_sqft" in df.columns else n_all
    zero_per_unit = (df.get("per_unit_sqft", pd.Series([0])) == 0).sum()
    log.info("per_unit_sqft: null=%d  zero=%d  median=%.0f",
             null_per_unit, zero_per_unit,
             df["per_unit_sqft"].median() if "per_unit_sqft" in df.columns else 0)

    return df


def validate_and_split(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    missing = [f for f in UNIT_FEATURES if f not in df.columns]
    if missing:
        log.error("Missing feature columns: %s", missing)
        log.error("  Re-run: python data/engineer_features.py")
        sys.exit(1)
    if TARGET not in df.columns:
        log.error("Target '%s' not in dataset", TARGET)
        sys.exit(1)
    if "is_test" not in df.columns:
        log.error("'is_test' column missing — re-run engineer_features.py")
        sys.exit(1)

    train = df[df["is_test"] == 0].copy()
    test = df[df["is_test"] == 1].copy()
    log.info("Train: %s rows  |  Test: %s rows", f"{len(train):,}", f"{len(test):,}")

    log.info("Borough distribution (train):")
    for borough in ["Bronx", "Brooklyn", "Manhattan", "Queens", "Staten_Island"]:
        col = f"borough_{borough}"
        if col in train.columns:
            n = int(train[col].sum())
            log.info("  %-15s %s", borough, f"{n:,}")

    return train, test


def train_model(
    train: pd.DataFrame,
    test: pd.DataFrame,
) -> xgb.XGBRegressor:
    X_train = _to_numeric(train[UNIT_FEATURES])
    y_train = train[TARGET]
    X_test = _to_numeric(test[UNIT_FEATURES]) if len(test) > 0 else None
    y_test = test[TARGET] if len(test) > 0 else None

    params = dict(XGB_PARAMS)
    if X_test is not None:
        params["early_stopping_rounds"] = EARLY_STOPPING_ROUNDS

    model = xgb.XGBRegressor(**params)
    eval_set = [(X_test, y_test)] if X_test is not None else []

    log.info("Training unit AVM (n_estimators=%d, lr=%.3f, max_depth=%d) …",
             params["n_estimators"], params["learning_rate"], params["max_depth"])
    model.fit(X_train, y_train, eval_set=eval_set, verbose=100)

    best = getattr(model, "best_iteration", None)
    if best is not None:
        log.info("Best iteration: %d", best)
    return model


def evaluate(model: xgb.XGBRegressor, test: pd.DataFrame) -> None:
    if len(test) == 0:
        log.warning("No test rows — skipping evaluation.")
        return

    X_test = _to_numeric(test[UNIT_FEATURES])
    y_true = test[TARGET].to_numpy()
    y_pred = model.predict(X_test)

    mape_val = mape(y_true, y_pred)
    median_ape_val = median_ape(y_true, y_pred)
    r2 = r2_score(y_true, y_pred)
    rmse = root_mean_squared_error(y_true, y_pred)

    log.info("=" * 50)
    log.info("Unit model — test-set evaluation (2023+ sales, n=%s):", f"{len(test):,}")
    log.info("  MAPE:        %.1f%%  (target <20%%)", mape_val)
    log.info("  Median APE:  %.1f%%", median_ape_val)
    log.info("  R²:          %.4f", r2)
    log.info("  RMSE:        $%s", f"{rmse:,.0f}")
    log.info("=" * 50)

    if mape_val < 20:
        log.info("MAPE %.1f%% — stretch goal achieved!", mape_val)
    elif mape_val < 30:
        log.info("MAPE %.1f%% — acceptance criteria met (<30%%).", mape_val)
    else:
        log.warning("MAPE %.1f%% — above 30%% target.", mape_val)

    # Sample predictions
    sample = test.head(10).copy()
    sample["predicted"] = model.predict(_to_numeric(sample[UNIT_FEATURES]))
    sample["pct_error"] = (
        (sample["predicted"] - sample[TARGET]) / sample[TARGET] * 100
    ).round(1)
    log.info("Sample predictions (first 10 test rows):")
    log.info("  %-12s  %-12s  %s", "Actual", "Predicted", "% Error")
    for _, row in sample.iterrows():
        log.info("  $%-11s  $%-11s  %+.1f%%",
                 f"{row[TARGET]:,.0f}",
                 f"{row['predicted']:,.0f}",
                 row["pct_error"])


def save_artifacts(model: xgb.XGBRegressor) -> None:
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    model.save_model(MODEL_PATH)
    log.info("Saved model → %s (%.1f MB)",
             MODEL_PATH, MODEL_PATH.stat().st_size / 1_048_576)
    with open(FEATURE_COLS_PATH, "w") as fh:
        json.dump(UNIT_FEATURES, fh, indent=2)
    log.info("Saved feature columns → %s", FEATURE_COLS_PATH)


def run(dry_run: bool = False) -> None:
    df = load_and_filter()
    train_df, test_df = validate_and_split(df)

    log.info("Feature null rates (train set):")
    null_rates = train_df[UNIT_FEATURES].isnull().mean().sort_values(ascending=False)
    for feat, rate in null_rates[null_rates > 0].items():
        log.info("  %-25s  %.1f%% null", feat, rate * 100)
    if (null_rates == 0).all():
        log.info("  (no nulls)")

    log.info("Target (sale_price) — train: mean $%s  median $%s",
             f"{train_df[TARGET].mean():,.0f}", f"{train_df[TARGET].median():,.0f}")

    if dry_run:
        log.info("Dry run — no training or files written.")
        return

    if len(train_df) < 100:
        log.error("Too few training rows (%d). Run ingestion pipeline first.", len(train_df))
        sys.exit(1)

    model = train_model(train_df, test_df)
    evaluate(model, test_df)
    save_artifacts(model)
    log.info("Done. Artifacts in %s/", MODELS_DIR)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Train unit-level XGBoost AVM for condo/co-op (Option C / TES-118)"
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="Validate data and print stats without training")
    args = parser.parse_args()

    log.info("=== Unit AVM training (avm_unit_v1) ===")
    log.info("Input:    %s (filtered to GFA=0 rows)", INPUT_PATH)
    log.info("Model:    %s", MODEL_PATH)
    log.info("Features: %d", len(UNIT_FEATURES))

    run(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
