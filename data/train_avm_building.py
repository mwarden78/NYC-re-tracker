"""Train building-level AVM — Option C / TES-119.

Reads data/training/features_engineered.parquet (GFA > 0 rows, produced by
engineer_features.py) and trains an XGBoost regression model for whole-building
property sales (1-4 family, multifamily, mixed-use). Includes per_unit_sqft
as an additional feature vs. the v1 model.

Artifacts saved to data/models/:
  avm_building_v1.json           — XGBoost model (native JSON)
  feature_columns_building_v1.json — ordered feature list for inference

Prerequisites:
  python data/engineer_features.py   # produces features_engineered.parquet

Usage:
  python data/train_avm_building.py           # full training run
  python data/train_avm_building.py --dry-run # validate data, no training
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
INPUT_PATH = DATA_DIR / "features_engineered.parquet"        # building-only (GFA > 0)
MODEL_PATH = MODELS_DIR / "avm_building_v1.json"
FEATURE_COLS_PATH = MODELS_DIR / "feature_columns_building_v1.json"

BUILDING_FEATURES = [
    # Structural
    "gross_square_feet",
    "lotarea",
    "bldgarea",
    "numfloors",
    "pluto_unitsres",
    "sale_total_units",
    "per_unit_sqft",         # avg unit size from PLUTO (new vs v1)
    # Derived
    "building_age_at_sale",
    "far_utilized_pct",
    "far_remaining",
    "is_mixed_use",
    # Temporal
    "sale_year",
    "sale_quarter",
    # Spatial
    "latitude",
    "longitude",
    # Categorical
    "bldgclass_enc",
    "zonedist_enc",
    # Borough one-hot
    "borough_Bronx",
    "borough_Brooklyn",
    "borough_Manhattan",
    "borough_Queens",
    "borough_Staten_Island",
]

TARGET = "sale_price"

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


def load_data() -> pd.DataFrame:
    if not INPUT_PATH.exists():
        log.error("Missing input: %s", INPUT_PATH)
        log.error("  Run: python data/engineer_features.py")
        sys.exit(1)
    df = pd.read_parquet(INPUT_PATH)
    log.info("Loaded %s rows, %d columns from %s",
             f"{len(df):,}", len(df.columns), INPUT_PATH)
    return df


def validate_and_split(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    missing = [f for f in BUILDING_FEATURES if f not in df.columns]
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
    return train, test


def train_model(
    train: pd.DataFrame,
    test: pd.DataFrame,
) -> xgb.XGBRegressor:
    X_train = _to_numeric(train[BUILDING_FEATURES])
    y_train = train[TARGET]
    X_test = _to_numeric(test[BUILDING_FEATURES]) if len(test) > 0 else None
    y_test = test[TARGET] if len(test) > 0 else None

    params = dict(XGB_PARAMS)
    if X_test is not None:
        params["early_stopping_rounds"] = EARLY_STOPPING_ROUNDS

    model = xgb.XGBRegressor(**params)
    eval_set = [(X_test, y_test)] if X_test is not None else []

    log.info("Training building AVM (n_estimators=%d, lr=%.3f, max_depth=%d) …",
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

    X_test = _to_numeric(test[BUILDING_FEATURES])
    y_true = test[TARGET].to_numpy()
    y_pred = model.predict(X_test)

    mape_val = mape(y_true, y_pred)
    median_ape_val = median_ape(y_true, y_pred)
    r2 = r2_score(y_true, y_pred)
    rmse = root_mean_squared_error(y_true, y_pred)

    log.info("=" * 50)
    log.info("Building model — test-set evaluation (2023+ sales, n=%s):", f"{len(test):,}")
    log.info("  MAPE:        %.1f%%  (target <20%%)", mape_val)
    log.info("  Median APE:  %.1f%%", median_ape_val)
    log.info("  R²:          %.4f", r2)
    log.info("  RMSE:        $%s", f"{rmse:,.0f}")
    log.info("=" * 50)

    if mape_val < 20:
        log.info("MAPE %.1f%% — stretch goal (<20%%) achieved!", mape_val)
    elif mape_val < 30:
        log.info("MAPE %.1f%% — v1 acceptance criteria met (<30%%).", mape_val)
    else:
        log.warning("MAPE %.1f%% — above 30%% target.", mape_val)


def save_artifacts(model: xgb.XGBRegressor) -> None:
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    model.save_model(MODEL_PATH)
    log.info("Saved model → %s (%.1f MB)",
             MODEL_PATH, MODEL_PATH.stat().st_size / 1_048_576)
    with open(FEATURE_COLS_PATH, "w") as fh:
        json.dump(BUILDING_FEATURES, fh, indent=2)
    log.info("Saved feature columns → %s", FEATURE_COLS_PATH)


def run(dry_run: bool = False) -> None:
    df = load_data()
    train_df, test_df = validate_and_split(df)

    log.info("Feature null rates (train set):")
    null_rates = train_df[BUILDING_FEATURES].isnull().mean().sort_values(ascending=False)
    for feat, rate in null_rates[null_rates > 0].items():
        log.info("  %-25s  %.1f%% null", feat, rate * 100)
    if (null_rates == 0).all():
        log.info("  (no nulls)")

    log.info("Target (sale_price) — train: mean $%s  median $%s",
             f"{train_df[TARGET].mean():,.0f}", f"{train_df[TARGET].median():,.0f}")

    if dry_run:
        log.info("Dry run — no training or files written.")
        return

    if len(train_df) < 10:
        log.error("Too few training rows (%d). Run ingestion pipeline first.", len(train_df))
        sys.exit(1)

    model = train_model(train_df, test_df)
    evaluate(model, test_df)
    save_artifacts(model)
    log.info("Done. Artifacts in %s/", MODELS_DIR)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Train building-level XGBoost AVM (Option C / TES-119)"
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="Validate data and print stats without training")
    args = parser.parse_args()

    log.info("=== Building AVM training (avm_building_v1) ===")
    log.info("Input:    %s", INPUT_PATH)
    log.info("Model:    %s", MODEL_PATH)
    log.info("Features: %d", len(BUILDING_FEATURES))

    run(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
