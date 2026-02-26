"""Train and evaluate baseline XGBoost AVM — TES-71.

Reads data/training/features_engineered.parquet (produced by TES-70),
trains an XGBoost regression model to predict NYC property sale price,
evaluates on the held-out 2023+ temporal test set, and saves artifacts.

Artifacts saved to data/models/:
  avm_v1.json           — XGBoost model (native JSON format)
  feature_columns.json  — ordered feature list for inference pipeline
  shap_summary.png      — SHAP feature importance plot

Prerequisites:
  python data/ingest_dof_sales.py
  python data/download_pluto.py
  python data/build_training_dataset.py
  python data/engineer_features.py

Usage:
  python data/train_avm.py            # full training run
  python data/train_avm.py --dry-run  # validate data, print split stats, no training
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

try:
    import matplotlib
    matplotlib.use("Agg")  # headless — no display needed
    import matplotlib.pyplot as plt
    import shap
    _SHAP_AVAILABLE = True
except ImportError:
    _SHAP_AVAILABLE = False

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

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

DATA_DIR = Path(__file__).parent / "training"
MODELS_DIR = Path(__file__).parent / "models"
INPUT_PATH = DATA_DIR / "features_engineered.parquet"
MODEL_PATH = MODELS_DIR / "avm_v1.json"
FEATURE_COLS_PATH = MODELS_DIR / "feature_columns.json"
SHAP_PLOT_PATH = MODELS_DIR / "shap_summary.png"

# ---------------------------------------------------------------------------
# Feature definition
# These must match the column names produced by engineer_features.py (TES-70).
# ---------------------------------------------------------------------------

FEATURES = [
    # Structural (from DOF + PLUTO)
    "gross_square_feet",
    "lotarea",
    "bldgarea",
    "numfloors",
    "pluto_unitsres",
    "sale_total_units",
    # Derived by engineer_features.py
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
    # Categorical (label-encoded by engineer_features.py)
    "bldgclass_enc",
    "zonedist_enc",
    # Borough (one-hot encoded by engineer_features.py)
    "borough_Bronx",
    "borough_Brooklyn",
    "borough_Manhattan",
    "borough_Queens",
    "borough_Staten_Island",
]

TARGET = "sale_price"

# XGBoost hyperparameters — sensible v1 defaults, tune in a later ticket
XGB_PARAMS = dict(
    n_estimators=1000,
    learning_rate=0.05,
    max_depth=6,
    subsample=0.8,
    colsample_bytree=0.8,
    eval_metric="mape",
    tree_method="hist",   # fast histogram method
    random_state=42,
    n_jobs=-1,
)
EARLY_STOPPING_ROUNDS = 50

# Minimum rows in test set to run SHAP (expensive on tiny sets)
MIN_SHAP_ROWS = 50


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def mape(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    """Mean absolute percentage error (0–100 scale)."""
    mask = y_true > 0
    return float(np.mean(np.abs((y_true[mask] - y_pred[mask]) / y_true[mask])) * 100)


def median_ape(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    """Median absolute percentage error (0–100 scale)."""
    mask = y_true > 0
    return float(np.median(np.abs((y_true[mask] - y_pred[mask]) / y_true[mask])) * 100)


def check_features(df: pd.DataFrame) -> list[str]:
    """Return list of FEATURES that are missing from df."""
    return [f for f in FEATURES if f not in df.columns]


# ---------------------------------------------------------------------------
# Training pipeline
# ---------------------------------------------------------------------------

def load_data() -> pd.DataFrame:
    if not INPUT_PATH.exists():
        log.error("Missing input: %s", INPUT_PATH)
        log.error("  Run: python data/engineer_features.py")
        sys.exit(1)
    df = pd.read_parquet(INPUT_PATH)
    log.info("Loaded %s rows, %d columns from %s", f"{len(df):,}", len(df.columns), INPUT_PATH)
    return df


def validate_and_split(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Validate feature presence and split into train/test."""
    missing = check_features(df)
    if missing:
        log.error("Missing feature columns in dataset: %s", missing)
        log.error("  Re-run: python data/engineer_features.py")
        sys.exit(1)

    if TARGET not in df.columns:
        log.error("Target column '%s' not found in dataset", TARGET)
        sys.exit(1)

    if "is_test" not in df.columns:
        log.error("'is_test' column missing — re-run engineer_features.py")
        sys.exit(1)

    train = df[df["is_test"] == 0].copy()
    test = df[df["is_test"] == 1].copy()

    log.info("Train: %s rows (sale < 2023-01-01)", f"{len(train):,}")
    log.info("Test:  %s rows (sale ≥ 2023-01-01)", f"{len(test):,}")

    if len(train) < 100:
        log.warning("Only %d training rows — model quality will be poor. "
                    "Run full ingestion first.", len(train))
    if len(test) == 0:
        log.warning("No test rows found. Evaluation metrics will be skipped.")

    return train, test


def _to_numeric(df: pd.DataFrame) -> pd.DataFrame:
    """Cast all feature columns to float64, coercing non-numeric strings to NaN."""
    return df.apply(pd.to_numeric, errors="coerce")


def train(
    train: pd.DataFrame,
    test: pd.DataFrame,
) -> xgb.XGBRegressor:
    """Fit XGBoost model with early stopping on the temporal test set."""
    X_train = _to_numeric(train[FEATURES])
    y_train = train[TARGET]
    X_test = _to_numeric(test[FEATURES]) if len(test) > 0 else None
    y_test = test[TARGET] if len(test) > 0 else None

    # Only use early stopping when a validation set is available
    params = dict(XGB_PARAMS)
    if X_test is not None:
        params["early_stopping_rounds"] = EARLY_STOPPING_ROUNDS

    model = xgb.XGBRegressor(**params)

    eval_set = [(X_test, y_test)] if X_test is not None else []

    log.info("Training XGBoost (n_estimators=%d, lr=%.3f, max_depth=%d%s) …",
             params["n_estimators"], params["learning_rate"], params["max_depth"],
             f", early_stop={EARLY_STOPPING_ROUNDS}" if X_test is not None else "")

    model.fit(
        X_train, y_train,
        eval_set=eval_set,
        verbose=100,
    )

    best = getattr(model, "best_iteration", None)
    if best is not None:
        log.info("Best iteration: %d (early stopping at %d rounds)",
                 best, EARLY_STOPPING_ROUNDS)

    return model


def evaluate(model: xgb.XGBRegressor, test: pd.DataFrame) -> None:
    """Print evaluation metrics on the held-out test set."""
    if len(test) == 0:
        log.warning("No test rows — skipping evaluation.")
        return

    X_test = _to_numeric(test[FEATURES])
    y_true = test[TARGET].to_numpy()
    y_pred = model.predict(X_test)

    mape_val = mape(y_true, y_pred)
    median_ape_val = median_ape(y_true, y_pred)
    r2 = r2_score(y_true, y_pred)
    rmse = root_mean_squared_error(y_true, y_pred)

    log.info("=" * 50)
    log.info("Test-set evaluation (2023+ sales, n=%s):", f"{len(test):,}")
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
        log.warning("MAPE %.1f%% — above 30%% target. Consider more data or tuning.", mape_val)

    # Sample predictions
    sample = test.head(10).copy()
    sample["predicted"] = model.predict(_to_numeric(sample[FEATURES]))
    sample["pct_error"] = ((sample["predicted"] - sample[TARGET]) / sample[TARGET] * 100).round(1)
    log.info("Sample predictions (first 10 test rows):")
    log.info("  %-12s  %-12s  %-12s  %s", "Actual", "Predicted", "% Error", "Address")
    for _, row in sample.iterrows():
        log.info("  $%-11s  $%-11s  %+.1f%%  %s",
                 f"{row[TARGET]:,.0f}",
                 f"{row['predicted']:,.0f}",
                 row["pct_error"],
                 row.get("sale_address", ""))


def save_shap(model: xgb.XGBRegressor, test: pd.DataFrame) -> None:
    """Generate and save SHAP feature importance summary plot."""
    if not _SHAP_AVAILABLE:
        log.warning("shap/matplotlib not available — skipping SHAP plot.")
        return

    if len(test) < MIN_SHAP_ROWS:
        log.warning("Only %d test rows — skipping SHAP plot (need ≥%d).",
                    len(test), MIN_SHAP_ROWS)
        return

    X_shap = _to_numeric(test[FEATURES]).head(1000)  # cap at 1k for speed
    log.info("Computing SHAP values on %d rows …", len(X_shap))

    explainer = shap.TreeExplainer(model)
    shap_values = explainer.shap_values(X_shap)

    plt.figure(figsize=(10, 8))
    shap.summary_plot(shap_values, X_shap, feature_names=FEATURES, show=False)
    plt.tight_layout()
    plt.savefig(SHAP_PLOT_PATH, dpi=150, bbox_inches="tight")
    plt.close()
    log.info("Saved SHAP summary plot → %s", SHAP_PLOT_PATH)


def save_artifacts(model: xgb.XGBRegressor) -> None:
    """Save model JSON and feature column list."""
    MODELS_DIR.mkdir(parents=True, exist_ok=True)

    model.save_model(MODEL_PATH)
    log.info("Saved model → %s (%.1f MB)",
             MODEL_PATH, MODEL_PATH.stat().st_size / 1_048_576)

    with open(FEATURE_COLS_PATH, "w") as fh:
        json.dump(FEATURES, fh, indent=2)
    log.info("Saved feature columns → %s", FEATURE_COLS_PATH)


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def run(dry_run: bool = False) -> None:
    df = load_data()
    train_df, test_df = validate_and_split(df)

    # Feature stats
    log.info("Feature null rates (train set):")
    null_rates = train_df[FEATURES].isnull().mean().sort_values(ascending=False)
    for feat, rate in null_rates[null_rates > 0].items():
        log.info("  %-25s  %.1f%% null", feat, rate * 100)
    if (null_rates == 0).all():
        log.info("  (no nulls in any feature column)")

    # Target distribution
    log.info("Target (sale_price) — train set:")
    log.info("  mean   $%s", f"{train_df[TARGET].mean():,.0f}")
    log.info("  median $%s", f"{train_df[TARGET].median():,.0f}")
    log.info("  min    $%s", f"{train_df[TARGET].min():,.0f}")
    log.info("  max    $%s", f"{train_df[TARGET].max():,.0f}")

    if dry_run:
        log.info("Dry run — no training or files written.")
        return

    if len(train_df) < 10:
        log.error("Too few training rows (%d) to fit a model. "
                  "Run full ingestion pipeline first.", len(train_df))
        sys.exit(1)

    model = train(train_df, test_df)
    evaluate(model, test_df)

    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    save_artifacts(model)
    save_shap(model, test_df)

    log.info("Done. Artifacts in %s/", MODELS_DIR)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Train and evaluate baseline XGBoost AVM"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Validate data and print split stats without training",
    )
    args = parser.parse_args()

    log.info("=== XGBoost AVM training ===")
    log.info("Input:   %s", INPUT_PATH)
    log.info("Model:   %s", MODEL_PATH)
    log.info("Features: %d", len(FEATURES))

    run(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
