#!/usr/bin/env python3
"""Score listings with dual-model AVM — Option C / TES-120.

Routes each listing to the appropriate XGBoost model based on property_type:
  - Condo / Co-op  → unit-level model  (avm_unit_v1.json)
  - All others     → building model    (avm_building_v1.json)

Falls back to the single v1 model (avm_v1.json) if the dual-model artifacts
are missing (e.g. models not yet trained).

Prerequisites (dual model):
  python data/engineer_features.py    # produces both parquet outputs
  python data/train_avm_building.py   # produces avm_building_v1.json
  python data/train_avm_unit.py       # produces avm_unit_v1.json

Usage:
  python data/score_listings.py              # score unscored listings only
  python data/score_listings.py --force      # rescore all listings
  python data/score_listings.py --dry-run    # print predictions, do not write
  python data/score_listings.py --limit N    # cap rows processed
  python data/score_listings.py --model v1   # force single v1 model
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

# --- Single v1 model (legacy / fallback) ---
V1_MODEL_PATH = MODELS_DIR / "avm_v1.json"
V1_ENCODERS_PATH = MODELS_DIR / "label_encoders.pkl"
V1_FEATURE_COLS_PATH = MODELS_DIR / "feature_columns.json"

# --- Building model (Option C) ---
BUILDING_MODEL_PATH = MODELS_DIR / "avm_building_v1.json"
BUILDING_ENCODERS_PATH = MODELS_DIR / "label_encoders.pkl"         # same as v1
BUILDING_FEATURE_COLS_PATH = MODELS_DIR / "feature_columns_building_v1.json"

# --- Unit model (Option C) ---
UNIT_MODEL_PATH = MODELS_DIR / "avm_unit_v1.json"
UNIT_ENCODERS_PATH = MODELS_DIR / "label_encoders_full.pkl"        # full encoder set
UNIT_FEATURE_COLS_PATH = MODELS_DIR / "feature_columns_unit_v1.json"

# Dual-model is "available" when both models and the unit encoder exist
DUAL_MODEL_AVAILABLE = (
    BUILDING_MODEL_PATH.exists()
    and UNIT_MODEL_PATH.exists()
    and UNIT_ENCODERS_PATH.exists()
    and BUILDING_FEATURE_COLS_PATH.exists()
    and UNIT_FEATURE_COLS_PATH.exists()
)

# Property types that go to the unit model
UNIT_PROPERTY_TYPES = {"condo", "co-op", "co op", "cooperative", "condominium"}

_TODAY = date.today()
CURRENT_YEAR = _TODAY.year
CURRENT_QUARTER = (_TODAY.month - 1) // 3 + 1
TRAIN_YEAR_MAX = 2024   # TES-102: clamp to training window

MEDIAN_FILLS: dict[str, float] = {
    "numfloors": 4.0,
    "pluto_unitsres": 6.0,
    "sale_total_units": 6.0,
    "far_utilized_pct": 0.7,
    "far_remaining": 0.1,
    "lotarea": 2500.0,
    "bldgarea": 3000.0,
    "per_unit_sqft": 800.0,
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _simplify_bldgclass(val: object) -> str:
    if pd.isna(val) or not str(val).strip():
        return "?"
    return str(val).strip()[0].upper()


def _simplify_zonedist(val: object) -> str:
    if pd.isna(val) or not str(val).strip():
        return "?"
    cleaned = str(val).strip().upper()
    m = re.match(r"^([A-Z]+\d+)", cleaned)
    if m:
        return m.group(1)
    letters = re.sub(r"[^A-Z]", "", cleaned)
    return letters or "?"


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
    return mapping.get(val, 0)


def _is_unit_listing(row: dict[str, Any]) -> bool:
    """Return True if this listing should be scored by the unit model."""
    pt = str(row.get("property_type") or "").lower().strip()
    return any(kw in pt for kw in UNIT_PROPERTY_TYPES)


def _add_utils_to_path() -> None:
    repo_root = str(Path(__file__).parent.parent)
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)


# ---------------------------------------------------------------------------
# Artifact loading
# ---------------------------------------------------------------------------

def _load_artifacts(
    model_path: Path,
    encoders_path: Path,
    feature_cols_path: Path,
    label: str,
) -> tuple[xgb.XGBRegressor, dict[str, dict[str, int]], list[str]]:
    missing = [p for p in (model_path, encoders_path, feature_cols_path) if not p.exists()]
    if missing:
        log.error("Missing %s artifact(s):", label)
        for p in missing:
            log.error("  %s", p)
        sys.exit(1)

    model = xgb.XGBRegressor()
    model.load_model(model_path)
    log.info("[%s] Loaded model from %s", label, model_path.name)

    with open(encoders_path, "rb") as fh:
        encoders: dict[str, dict[str, int]] = pickle.load(fh)
    log.info("[%s] Loaded encoders (%d categorical features)", label, len(encoders))

    with open(feature_cols_path) as fh:
        feature_cols: list[str] = json.load(fh)
    log.info("[%s] Feature columns: %d", label, len(feature_cols))

    return model, encoders, feature_cols


def load_dual_artifacts() -> tuple[
    tuple[xgb.XGBRegressor, dict, list],
    tuple[xgb.XGBRegressor, dict, list],
]:
    building = _load_artifacts(
        BUILDING_MODEL_PATH, BUILDING_ENCODERS_PATH, BUILDING_FEATURE_COLS_PATH, "building"
    )
    unit = _load_artifacts(
        UNIT_MODEL_PATH, UNIT_ENCODERS_PATH, UNIT_FEATURE_COLS_PATH, "unit"
    )
    return building, unit


def load_v1_artifacts() -> tuple[xgb.XGBRegressor, dict[str, dict[str, int]], list[str]]:
    return _load_artifacts(V1_MODEL_PATH, V1_ENCODERS_PATH, V1_FEATURE_COLS_PATH, "v1")


# ---------------------------------------------------------------------------
# Supabase
# ---------------------------------------------------------------------------

def fetch_listings(
    force: bool, limit: int | None, offset: int = 0
) -> list[dict[str, Any]]:
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
    if offset:
        query = query.range(offset, offset + (limit or 1000) - 1)

    response = query.execute()
    rows: list[dict[str, Any]] = response.data or []
    log.info("Fetched %d listing row(s) to score (offset=%d)", len(rows), offset)
    return rows


# ---------------------------------------------------------------------------
# Feature builders
# ---------------------------------------------------------------------------

def _common_fields(row: dict[str, Any]) -> dict[str, float]:
    """Fields shared between building and unit feature vectors."""
    r: dict[str, float] = {}

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

    # TES-102: clamp sale_year to training window
    r["sale_year"] = float(min(CURRENT_YEAR, TRAIN_YEAR_MAX))
    r["sale_quarter"] = float(CURRENT_QUARTER)

    r["latitude"] = _float(row.get("latitude")) or 40.7128
    r["longitude"] = _float(row.get("longitude")) or -74.006

    return r


def _encode_cat_and_borough(
    row: dict[str, Any],
    r: dict[str, float],
    encoders: dict[str, dict[str, int]],
) -> None:
    """Encode bldgclass + zonedist1 + borough into r in-place."""
    bldgclass_simple = _simplify_bldgclass(row.get("bldgclass"))
    r["bldgclass_enc"] = float(_label_encode(bldgclass_simple, encoders.get("bldgclass", {})))

    zonedist_simple = _simplify_zonedist(row.get("zonedist1"))
    r["zonedist_enc"] = float(_label_encode(zonedist_simple, encoders.get("zonedist1", {})))

    borough_raw = str(row.get("borough") or "").strip()
    borough_alias: dict[str, str] = {
        "BK": "Brooklyn", "BROOKLYN": "Brooklyn",
        "MN": "Manhattan", "MANHATTAN": "Manhattan",
        "QN": "Queens", "QUEENS": "Queens",
        "BX": "Bronx", "BRONX": "Bronx",
        "SI": "Staten_Island",
        "STATEN ISLAND": "Staten_Island", "STATEN_ISLAND": "Staten_Island",
    }
    borough_norm = borough_alias.get(borough_raw.upper(), borough_raw.replace(" ", "_"))
    for b in ["Bronx", "Brooklyn", "Manhattan", "Queens", "Staten_Island"]:
        r[f"borough_{b}"] = 1.0 if borough_norm == b else 0.0


def build_building_features(
    rows: list[dict[str, Any]],
    encoders: dict[str, dict[str, int]],
    feature_cols: list[str],
) -> pd.DataFrame:
    """Feature vector for building-level listings (1-4 family, multifamily)."""
    records: list[dict[str, float]] = []

    for row in rows:
        r = _common_fields(row)

        sqft = _float(row.get("sqft"))
        lot_sqft = _float(row.get("lot_sqft"))
        units_res = _float(row.get("units_res"))
        units_total = _float(row.get("units_total"))

        r["gross_square_feet"] = sqft if sqft else MEDIAN_FILLS["bldgarea"]
        r["bldgarea"] = r["gross_square_feet"]
        r["lotarea"] = lot_sqft if lot_sqft else MEDIAN_FILLS["lotarea"]

        num_floors = _float(row.get("num_floors"))
        r["numfloors"] = num_floors if num_floors else MEDIAN_FILLS["numfloors"]

        r["pluto_unitsres"] = units_res if units_res else MEDIAN_FILLS["pluto_unitsres"]
        r["sale_total_units"] = units_total if units_total else MEDIAN_FILLS["sale_total_units"]

        # per_unit_sqft: building sqft / residential units (mirrors training derivation)
        safe_units = max(units_res or 1.0, 1.0)
        r["per_unit_sqft"] = (r["gross_square_feet"] / safe_units) if r["gross_square_feet"] > 0 \
            else MEDIAN_FILLS["per_unit_sqft"]

        # TES-103: derive is_mixed_use from bldgclass (S* = mixed res/commercial)
        bldgclass_raw = str(row.get("bldgclass") or "").strip().upper()
        r["is_mixed_use"] = 1.0 if bldgclass_raw.startswith("S") else 0.0

        _encode_cat_and_borough(row, r, encoders)
        records.append(r)

    df = pd.DataFrame(records)
    for col in feature_cols:
        if col not in df.columns:
            log.warning("[building] Missing feature %s — filling 0", col)
            df[col] = 0.0
    return df[feature_cols].astype(float).fillna(0.0)


def build_unit_features(
    rows: list[dict[str, Any]],
    encoders: dict[str, dict[str, int]],
    feature_cols: list[str],
) -> pd.DataFrame:
    """Feature vector for unit-level listings (condo, co-op).

    Key difference from building features: per_unit_sqft = RentCast 'sqft'
    (actual unit interior sqft), not building-average.
    """
    records: list[dict[str, float]] = []

    for row in rows:
        r = _common_fields(row)

        sqft = _float(row.get("sqft"))
        units_res = _float(row.get("units_res"))
        num_floors = _float(row.get("num_floors"))

        # Core unit feature: RentCast sqft = actual unit interior area
        r["per_unit_sqft"] = sqft if sqft else MEDIAN_FILLS["per_unit_sqft"]

        r["numfloors"] = num_floors if num_floors else MEDIAN_FILLS["numfloors"]
        r["pluto_unitsres"] = units_res if units_res else MEDIAN_FILLS["pluto_unitsres"]

        _encode_cat_and_borough(row, r, encoders)
        records.append(r)

    df = pd.DataFrame(records)
    for col in feature_cols:
        if col not in df.columns:
            log.warning("[unit] Missing feature %s — filling 0", col)
            df[col] = 0.0
    return df[feature_cols].astype(float).fillna(0.0)


def build_features(
    rows: list[dict[str, Any]],
    encoders: dict[str, dict[str, int]],
    feature_cols: list[str],
) -> pd.DataFrame:
    """Legacy single-model feature builder (v1 backward compat)."""
    return build_building_features(rows, encoders, feature_cols)


# ---------------------------------------------------------------------------
# Write results
# ---------------------------------------------------------------------------

def write_scores(
    client: Any,
    rows: list[dict[str, Any]],
    preds: "np.ndarray[Any, Any]",
    model_vers: list[str],
) -> int:
    updates: list[dict[str, Any]] = []
    for row, pred, model_ver in zip(rows, preds, model_vers):
        predicted_value = int(round(float(pred)))
        price = _float(row.get("price"))
        value_ratio: float | None
        if price and price > 0:
            value_ratio = round(predicted_value / price, 2)
        else:
            value_ratio = None
        updates.append({
            "id": row["id"],
            "rentcast_id": row["rentcast_id"],
            "address": row["address"],
            "predicted_value": predicted_value,
            "value_ratio": value_ratio,
            "avm_model_ver": model_ver,
        })

    if not updates:
        return 0

    client.table("listings").upsert(updates, on_conflict="rentcast_id").execute()
    return len(updates)


def log_distribution(
    rows: list[dict[str, Any]],
    preds: "np.ndarray[Any, Any]",
    label: str = "",
) -> None:
    ratios: list[float] = []
    for row, pred in zip(rows, preds):
        price = _float(row.get("price"))
        if price and price > 0:
            ratios.append(float(pred) / price)

    if not ratios:
        log.warning("No listings had a valid price — cannot compute value_ratio stats")
        return

    arr = np.array(ratios)
    pct_above = float((arr > 1.15).mean() * 100)
    tag = f" [{label}]" if label else ""

    log.info("=" * 50)
    log.info("Value ratio distribution%s (n=%d):", tag, len(arr))
    log.info("  Median : %.3f", float(np.median(arr)))
    log.info("  P25    : %.3f", float(np.percentile(arr, 25)))
    log.info("  P75    : %.3f", float(np.percentile(arr, 75)))
    log.info("  > 1.15 : %.1f%%  (potential value-add opportunities)", pct_above)
    log.info("=" * 50)


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

def run_dual(force: bool, dry_run: bool, limit: int | None, offset: int = 0) -> None:
    """Score listings using building + unit models (Option C)."""
    log.info("Mode: DUAL MODEL (building_v1 + unit_v1)")
    building_artifacts, unit_artifacts = load_dual_artifacts()
    building_model, building_enc, building_cols = building_artifacts
    unit_model, unit_enc, unit_cols = unit_artifacts

    rows = fetch_listings(force=force, limit=limit, offset=offset)
    if not rows:
        log.info("No listings to score — nothing to do.")
        return

    # Split by property type
    building_rows = [r for r in rows if not _is_unit_listing(r)]
    unit_rows = [r for r in rows if _is_unit_listing(r)]
    log.info("Routing: %d building listings → building model, %d unit listings → unit model",
             len(building_rows), len(unit_rows))

    # Score each group
    all_rows: list[dict[str, Any]] = []
    all_preds: list[float] = []
    all_model_vers: list[str] = []

    if building_rows:
        X_b = build_building_features(building_rows, building_enc, building_cols)
        preds_b = building_model.predict(X_b)
        log.info("Scored %d building listing(s)", len(preds_b))
        all_rows.extend(building_rows)
        all_preds.extend(preds_b.tolist())
        all_model_vers.extend(["building_v1"] * len(building_rows))

    if unit_rows:
        X_u = build_unit_features(unit_rows, unit_enc, unit_cols)
        preds_u = unit_model.predict(X_u)
        log.info("Scored %d unit listing(s)", len(preds_u))
        all_rows.extend(unit_rows)
        all_preds.extend(preds_u.tolist())
        all_model_vers.extend(["unit_v1"] * len(unit_rows))

    preds_arr = np.array(all_preds)

    if dry_run:
        log.info("Dry run — sample predictions (first 20):")
        log.info("  %-12s  %-10s  %-10s  %-12s  %s",
                 "Model", "List Price", "Predicted", "Value Ratio", "Address")
        for row, pred, ver in list(zip(all_rows, preds_arr, all_model_vers))[:20]:
            price_val = _float(row.get("price"))
            price_str = f"{price_val:,.0f}" if price_val else "N/A"
            ratio_str = f"{pred / price_val:.2f}" if price_val else "N/A"
            log.info("  %-12s  $%-9s  $%-9s  %-12s  %s",
                     ver, price_str, f"{int(pred):,}", ratio_str, row.get("address", ""))
    else:
        _add_utils_to_path()
        from utils.supabase_client import get_client  # type: ignore[import]

        client = get_client()
        written = write_scores(client, all_rows, preds_arr, all_model_vers)
        log.info("Upserted %d row(s) to listings", written)

    log_distribution(all_rows, preds_arr, "combined")
    if building_rows:
        log_distribution(building_rows, np.array(all_preds[:len(building_rows)]), "building_v1")
    if unit_rows:
        log_distribution(unit_rows, np.array(all_preds[len(building_rows):]), "unit_v1")


def run_v1(force: bool, dry_run: bool, limit: int | None, offset: int = 0) -> None:
    """Score listings using single v1 model (legacy / fallback)."""
    log.info("Mode: SINGLE MODEL (v1)")
    model, encoders, feature_cols = load_v1_artifacts()

    rows = fetch_listings(force=force, limit=limit, offset=offset)
    if not rows:
        log.info("No listings to score — nothing to do.")
        return

    X = build_features(rows, encoders, feature_cols)
    preds = model.predict(X)
    log.info("Scored %d listing(s)", len(preds))
    model_vers = ["v1"] * len(rows)

    if dry_run:
        log.info("Dry run — sample predictions (first 20):")
        log.info("  %-36s  %-12s  %-10s  %s", "ID", "List Price", "Predicted", "Address")
        for row, pred in list(zip(rows, preds))[:20]:
            price_val = _float(row.get("price"))
            price_str = f"{price_val:,.0f}" if price_val else "N/A"
            log.info("  %-36s  $%-11s  $%-9s  %s",
                     row.get("id", ""), price_str, f"{int(pred):,}", row.get("address", ""))
    else:
        _add_utils_to_path()
        from utils.supabase_client import get_client  # type: ignore[import]

        client = get_client()
        written = write_scores(client, rows, preds, model_vers)
        log.info("Upserted %d row(s) to listings", written)

    log_distribution(rows, preds)


def run(
    force: bool, dry_run: bool, limit: int | None, model: str = "auto", offset: int = 0
) -> None:
    if model == "v1" or (model == "auto" and not DUAL_MODEL_AVAILABLE):
        if model == "auto":
            log.warning("Dual-model artifacts not found — falling back to v1.")
            log.warning("  Run: python data/train_avm_building.py && python data/train_avm_unit.py")
        run_v1(force=force, dry_run=dry_run, limit=limit, offset=offset)
    else:
        run_dual(force=force, dry_run=dry_run, limit=limit, offset=offset)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Score listings with dual-model AVM (Option C) or v1 fallback"
    )
    parser.add_argument("--force", action="store_true",
                        help="Rescore all listings, even those already scored")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print predictions without writing to the database")
    parser.add_argument("--limit", type=int, default=None, metavar="N",
                        help="Score at most N listings")
    parser.add_argument("--model", choices=["auto", "dual", "v1"], default="auto",
                        help="Model to use: auto (dual if available, else v1), dual, or v1")
    parser.add_argument("--offset", type=int, default=0, metavar="N",
                        help="Skip first N rows (for paginated --force rescoring)")
    args = parser.parse_args()

    mode = "DUAL" if (args.model == "dual" or
                      (args.model == "auto" and DUAL_MODEL_AVAILABLE)) else "V1"
    log.info("=== AVM scoring (mode=%s) ===", mode)
    log.info("Force:   %s", args.force)
    log.info("Dry run: %s", args.dry_run)
    log.info("Limit:   %s", args.limit if args.limit is not None else "none")
    log.info("Offset:  %s", args.offset)

    run(force=args.force, dry_run=args.dry_run, limit=args.limit,
        model=args.model, offset=args.offset)


if __name__ == "__main__":
    main()
