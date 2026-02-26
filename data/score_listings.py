#!/usr/bin/env python3
"""Score listings with AVM -- TES-75, TES-102, TES-103.

Reads rows from the Supabase `listings` table, builds the same feature
vector used during training (TES-71 / train_avm.py), runs the XGBoost
model, and writes `predicted_value`, `value_ratio`, and `avm_model_ver`
back to the database.

Prerequisites:
  python data/train_avm.py   # produces data/models/avm_v1.json + companions

Usage:
  python data/score_listings.py              # score unscored listings only
  python data/score_listings.py --force      # rescore all listings
  python data/score_listings.py --dry-run    # print predictions, do not write
  python data/score_listings.py --limit N    # cap rows processed
"""

from __future__ import annotations

import argparse
import json
import logging
import pickle
import re
import sys
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import xgboost as xgb

MODELS_DIR = Path(__file__).parent / "models"
MODEL_PATH = MODELS_DIR / "avm_v1.json"
ENCODERS_PATH = MODELS_DIR / "label_encoders.pkl"
FEATURE_COLS_PATH = MODELS_DIR / "feature_columns.json"

AVM_MODEL_VER = "v1"

_TODAY = date.today()
CURRENT_YEAR = _TODAY.year
CURRENT_QUARTER = (_TODAY.month - 1) // 3 + 1

MEDIAN_FILLS: dict[str, float] = {
    "numfloors": 4.0,
    "pluto_unitsres": 6.0,
    "sale_total_units": 6.0,
    "far_utilized_pct": 0.7,
    "far_remaining": 0.1,
    "lotarea": 2500.0,
    "bldgarea": 3000.0,
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

def _simplify_bldgclass(val: object) -> str:
    """Keep first character of building class (e.g. A1 -> A)."""
    if pd.isna(val) or not str(val).strip():
        return "?"
    return str(val).strip()[0].upper()


def _simplify_zonedist(val: object) -> str:
    """
    Reduce zoning district to letter prefix + first digit group.
    E.g. R6B -> R6, C4-2 -> C4, M1-6 -> M1.
    """
    if pd.isna(val) or not str(val).strip():
        return "?"
    cleaned = str(val).strip().upper()
    m = re.match(r"^([A-Z]+\d+)", cleaned)
    if m:
        return m.group(1)
    letters = re.sub(r"[^A-Z]", "", cleaned)
    return letters or "?"


def load_artifacts() -> tuple[xgb.XGBRegressor, dict[str, dict[str, int]], list[str]]:
    """Load model, label encoders, and feature column list.

    Exits with code 1 if any artifact is missing (model has not been trained yet).
    """
    missing = [p for p in (MODEL_PATH, ENCODERS_PATH, FEATURE_COLS_PATH) if not p.exists()]
    if missing:
        log.error("Missing model artifact(s):")
        for p in missing:
            log.error("  %s", p)
        log.error("Run:  python data/train_avm.py")
        sys.exit(1)

    model = xgb.XGBRegressor()
    model.load_model(MODEL_PATH)
    log.info("Loaded model from %s", MODEL_PATH)

    with open(ENCODERS_PATH, "rb") as fh:
        encoders: dict[str, dict[str, int]] = pickle.load(fh)
    log.info("Loaded label encoders (%d categorical features)", len(encoders))

    with open(FEATURE_COLS_PATH) as fh:
        feature_cols: list[str] = json.load(fh)
    log.info("Feature columns: %d", len(feature_cols))

    return model, encoders, feature_cols


def _add_utils_to_path() -> None:
    """Ensure the repo root is on sys.path so utils can be imported."""
    repo_root = str(Path(__file__).parent.parent)
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)


def fetch_listings(force: bool, limit: int | None) -> list[dict[str, Any]]:
    """Fetch listings rows from Supabase."""
    _add_utils_to_path()
    from utils.supabase_client import get_client  # type: ignore[import]

    client = get_client()

    query = client.table("listings").select(
        "id,rentcast_id,address,borough,zip_code,latitude,longitude,bbl,"
        "price,sqft,lot_sqft,beds,baths,property_type,year_built,"
        "bldgclass,zonedist1,residfar,builtfar,far_remaining,"
        "num_floors,units_res,units_total,predicted_value,status"
    )

    if not force:
        query = query.is_("predicted_value", "null")

    if limit is not None:
        query = query.limit(limit)

    response = query.execute()
    rows: list[dict[str, Any]] = response.data or []
    log.info("Fetched %d listing row(s) to score", len(rows))
    return rows


def _float(val: object) -> float | None:
    try:
        f = float(val)  # type: ignore[arg-type]
        return None if f != f else f  # NaN guard
    except (TypeError, ValueError):
        return None


def _int(val: object) -> int | None:
    try:
        return int(float(val))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _label_encode(val: str, mapping: dict[str, int]) -> int:
    """Look up val in mapping; fall back to 0 (unknown category) if unseen."""
    return mapping.get(val, 0)


def build_features(
    rows: list[dict[str, Any]],
    encoders: dict[str, dict[str, int]],
    feature_cols: list[str],
) -> pd.DataFrame:
    """Transform raw listings rows into a feature DataFrame aligned to feature_cols."""

    records: list[dict[str, float]] = []

    for row in rows:
        r: dict[str, float] = {}

        sqft = _float(row.get("sqft"))
        lot_sqft = _float(row.get("lot_sqft"))

        r["gross_square_feet"] = sqft if sqft else MEDIAN_FILLS["bldgarea"]
        r["bldgarea"] = r["gross_square_feet"]
        r["lotarea"] = lot_sqft if lot_sqft else MEDIAN_FILLS["lotarea"]

        num_floors = _float(row.get("num_floors"))
        r["numfloors"] = num_floors if num_floors else MEDIAN_FILLS["numfloors"]

        units_res = _float(row.get("units_res"))
        r["pluto_unitsres"] = units_res if units_res else MEDIAN_FILLS["pluto_unitsres"]

        units_total = _float(row.get("units_total"))
        r["sale_total_units"] = units_total if units_total else MEDIAN_FILLS["sale_total_units"]

        year_built = _int(row.get("year_built"))
        r["building_age_at_sale"] = float(CURRENT_YEAR - year_built) if year_built else 50.0

        residfar = _float(row.get("residfar"))
        builtfar = _float(row.get("builtfar"))
        if residfar and residfar > 0 and builtfar is not None:
            r["far_utilized_pct"] = min(builtfar / residfar, 1.0)
        else:
            r["far_utilized_pct"] = MEDIAN_FILLS["far_utilized_pct"]

        far_remaining = _float(row.get("far_remaining"))
        r["far_remaining"] = (
            far_remaining if far_remaining is not None else MEDIAN_FILLS["far_remaining"]
        )

        # TES-103: derive is_mixed_use from bldgclass (PLUTO-enriched on listings)
        # S* = mixed residential/commercial (S1-S5, S9); fallback 0 if unknown
        bldgclass_raw = str(row.get("bldgclass") or "").strip().upper()
        r["is_mixed_use"] = 1.0 if bldgclass_raw.startswith("S") else 0.0

        # TES-102: clamp sale_year to training range (max 2024) to avoid
        # XGBoost extrapolating price trends beyond the training window
        TRAIN_YEAR_MAX = 2024
        r["sale_year"] = float(min(CURRENT_YEAR, TRAIN_YEAR_MAX))
        r["sale_quarter"] = float(CURRENT_QUARTER)

        r["latitude"] = _float(row.get("latitude")) or 40.7128
        r["longitude"] = _float(row.get("longitude")) or -74.006

        bldgclass_simple = _simplify_bldgclass(row.get("bldgclass"))
        bldgclass_mapping = encoders.get("bldgclass", {})
        r["bldgclass_enc"] = float(_label_encode(bldgclass_simple, bldgclass_mapping))

        zonedist_simple = _simplify_zonedist(row.get("zonedist1"))
        zonedist_mapping = encoders.get("zonedist1", {})
        r["zonedist_enc"] = float(_label_encode(zonedist_simple, zonedist_mapping))

        borough_raw = str(row.get("borough") or "").strip()
        borough_alias: dict[str, str] = {
            "BK": "Brooklyn",
            "BROOKLYN": "Brooklyn",
            "MN": "Manhattan",
            "MANHATTAN": "Manhattan",
            "QN": "Queens",
            "QUEENS": "Queens",
            "BX": "Bronx",
            "BRONX": "Bronx",
            "SI": "Staten_Island",
            "STATEN ISLAND": "Staten_Island",
            "STATEN_ISLAND": "Staten_Island",
        }
        borough_norm = borough_alias.get(
            borough_raw.upper(), borough_raw.replace(" ", "_")
        )
        for b in ["Bronx", "Brooklyn", "Manhattan", "Queens", "Staten_Island"]:
            r[f"borough_{b}"] = 1.0 if borough_norm == b else 0.0

        records.append(r)

    df = pd.DataFrame(records)

    for col in feature_cols:
        if col not in df.columns:
            log.warning("Feature column missing from build: %s -- filling with 0", col)
            df[col] = 0.0

    df = df[feature_cols].astype(float).fillna(0.0)
    return df


def write_scores(
    client: Any,
    rows: list[dict[str, Any]],
    preds: "np.ndarray[Any, Any]",
) -> int:
    """Upsert predicted_value, value_ratio, avm_model_ver back to listings."""
    updates: list[dict[str, Any]] = []
    for row, pred in zip(rows, preds):
        predicted_value = int(round(float(pred)))
        price = _float(row.get("price"))
        value_ratio: float | None
        if price and price > 0:
            value_ratio = round(predicted_value / price, 2)
        else:
            value_ratio = None
        updates.append(
            {
                "id": row["id"],
                "rentcast_id": row["rentcast_id"],
                "address": row["address"],
                "predicted_value": predicted_value,
                "value_ratio": value_ratio,
                "avm_model_ver": AVM_MODEL_VER,
            }
        )

    if not updates:
        return 0

    # Use rentcast_id as conflict key (UNIQUE NOT NULL); include address to
    # satisfy NOT NULL constraint in the hypothetical INSERT path.
    client.table("listings").upsert(
        updates, on_conflict="rentcast_id"
    ).execute()
    return len(updates)


def log_distribution(
    rows: list[dict[str, Any]],
    preds: "np.ndarray[Any, Any]",
) -> None:
    """Log value_ratio distribution after scoring."""
    ratios: list[float] = []
    for row, pred in zip(rows, preds):
        price = _float(row.get("price"))
        if price and price > 0:
            ratios.append(float(pred) / price)

    if not ratios:
        log.warning("No listings had a valid price -- cannot compute value_ratio stats")
        return

    arr = np.array(ratios)
    pct_above = float((arr > 1.15).mean() * 100)

    log.info("=" * 50)
    log.info("Value ratio distribution (n=%d):", len(arr))
    log.info("  Median : %.3f", float(np.median(arr)))
    log.info("  P25    : %.3f", float(np.percentile(arr, 25)))
    log.info("  P75    : %.3f", float(np.percentile(arr, 75)))
    log.info("  > 1.15 : %.1f%%  (potential value-add opportunities)", pct_above)
    log.info("=" * 50)


def run(force: bool, dry_run: bool, limit: int | None) -> None:
    model, encoders, feature_cols = load_artifacts()

    rows = fetch_listings(force=force, limit=limit)
    if not rows:
        log.info("No listings to score -- nothing to do.")
        return

    X = build_features(rows, encoders, feature_cols)
    preds = model.predict(X)
    log.info("Scored %d listing(s)", len(preds))

    if dry_run:
        log.info("Dry run -- sample predictions (first 20):")
        log.info("  %-36s  %-12s  %-10s  %s", "ID", "List Price", "Predicted", "Address")
        for row, pred in list(zip(rows, preds))[:20]:
            price_val = _float(row.get("price"))
            price_str = f"{price_val:,.0f}" if price_val else "N/A"
            log.info(
                "  %-36s  $%-11s  $%-9s  %s",
                row.get("id", ""),
                price_str,
                f"{int(pred):,}",
                row.get("address", ""),
            )
    else:
        _add_utils_to_path()
        from utils.supabase_client import get_client  # type: ignore[import]

        client = get_client()
        written = write_scores(client, rows, preds)
        log.info("Upserted %d row(s) to listings", written)

    log_distribution(rows, preds)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Score listings with XGBoost AVM -- produces predicted_value and value_ratio"
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Rescore all listings, even those already scored",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print predictions without writing to the database",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        metavar="N",
        help="Score at most N listings",
    )
    args = parser.parse_args()

    log.info("=== AVM scoring (model %s) ===", AVM_MODEL_VER)
    log.info("Force:   %s", args.force)
    log.info("Dry run: %s", args.dry_run)
    log.info("Limit:   %s", args.limit if args.limit is not None else "none")

    run(force=args.force, dry_run=args.dry_run, limit=args.limit)


if __name__ == "__main__":
    main()
