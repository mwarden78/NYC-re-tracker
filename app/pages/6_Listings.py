"""Listings feed - RentCast for-sale listings ranked by AVM value ratio (TES-76)."""

from __future__ import annotations

import sys
import os

import streamlit as st

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from db import add_listing_to_pipeline, load_listings  # noqa: E402
from sidebar import render_more_section  # noqa: E402

st.set_page_config(page_title="Listings | NYC RE Tracker", page_icon="\U0001f3d8", layout="wide")
render_more_section()

st.title("NYC Listings")
st.caption("Active for-sale listings scored by AVM \u2014 highest arbitrage first")

# ---------------------------------------------------------------------------
# Load data
# ---------------------------------------------------------------------------
try:
    all_listings = load_listings()
except Exception as e:
    st.error(f"Could not load listings from Supabase. Check your `.env` file. ({e})")
    st.stop()

if not all_listings:
    st.info("No listings found. Run `python data/ingest_listings.py` to populate.")
    st.stop()

BOROUGHS = ["Brooklyn", "Queens", "Manhattan", "Bronx", "Staten Island"]
all_prop_types = sorted({li.get("property_type") for li in all_listings if li.get("property_type")})
has_value_ratio = any(li.get("value_ratio") is not None for li in all_listings)
price_floor = 100_000
price_ceil = 10_000_000

with st.sidebar:
    st.header("Filters")
    borough_sel = st.multiselect("Borough", BOROUGHS, placeholder="All boroughs")
    price_range = st.slider(
        "Price Range",
        min_value=price_floor,
        max_value=price_ceil,
        value=(price_floor, price_ceil),
        step=50_000,
        format="$%d",
    )
    prop_type_sel = st.multiselect(
        "Property Type", all_prop_types, placeholder="All types"
    )
    min_sqft = st.number_input("Min Sqft", min_value=0, value=0, step=100)
    if has_value_ratio:
        min_value_ratio = st.slider(
            "Min Value Ratio",
            min_value=1.0,
            max_value=2.0,
            value=1.0,
            step=0.05,
            format="%.2f",
        )
    else:
        min_value_ratio = 1.0
    scored_only = st.checkbox("Scored listings only", value=False)

# Apply filters
filtered: list[dict] = []
for listing in all_listings:
    if borough_sel and listing.get("borough") not in borough_sel:
        continue
    price = listing.get("price")
    if price is not None and not (price_range[0] <= price <= price_range[1]):
        continue
    if prop_type_sel and listing.get("property_type") not in prop_type_sel:
        continue
    sqft = listing.get("sqft")
    if min_sqft > 0 and (sqft is None or sqft < min_sqft):
        continue
    vr = listing.get("value_ratio")
    if has_value_ratio and vr is not None and vr < min_value_ratio:
        continue
    if scored_only and vr is None:
        continue
    filtered.append(listing)

# Summary line
total = len(all_listings)
shown = len(filtered)
scored = [li for li in filtered if li.get("value_ratio") is not None]
n_scored = len(scored)

summary_parts = [f"Showing **{shown}** of **{total}** listings"]
if n_scored:
    ratios = [li["value_ratio"] for li in scored]
    median_ratio = sorted(ratios)[len(ratios) // 2]
    summary_parts.append(
        f"**{n_scored}** scored by AVM (median value ratio: **{median_ratio:.2f}x**)"
    )
else:
    summary_parts.append("*None scored by AVM*")

st.caption("  \u00b7  ".join(summary_parts))

if shown == 0:
    st.warning("No listings match your current filters.")
    st.stop()


# Helpers
def _vr_indicator(value_ratio: float | None) -> str:
    if value_ratio is None:
        return "\u26ab Not scored"
    if value_ratio >= 1.15:
        return f"\U0001f7e2 {value_ratio:.2f}x"
    if value_ratio >= 1.05:
        return f"\U0001f7e1 {value_ratio:.2f}x"
    return f"\u26aa {value_ratio:.2f}x"


def _fmt_price(value: float | None) -> str:
    if value is None:
        return "\u2014"
    return f"${value:,.0f}"


def _fmt_num(value: float | None, unit: str = "") -> str:
    if value is None:
        return "\u2014"
    return f"{value:,.0f}{unit}"


# Listing cards - 3-column grid
cols = st.columns(3)

for i, listing in enumerate(filtered):
    listing_id = listing.get("id", "")
    address = listing.get("address", "Unknown address")
    borough = listing.get("borough", "")
    zip_code = listing.get("zip_code", "")
    price = listing.get("price")
    sqft = listing.get("sqft")
    price_per_sqft = listing.get("price_per_sqft")
    beds = listing.get("beds")
    baths = listing.get("baths")
    avm = listing.get("predicted_value")
    value_ratio = listing.get("value_ratio")
    zoning = listing.get("zonedist1")
    far_remaining = listing.get("far_remaining")
    year_built = listing.get("year_built")
    days_on_market = listing.get("days_on_market")

    detail_parts = []
    if beds is not None:
        detail_parts.append(f"{int(beds)}bd")
    if baths is not None:
        detail_parts.append(f"{baths:g}ba")
    beds_baths = "/".join(detail_parts) if detail_parts else ""

    sqft_str = _fmt_num(sqft, " sqft") if sqft else ""
    ppsf_str = f"${price_per_sqft:,.0f}/sqft" if price_per_sqft else ""
    avm_str = _fmt_price(avm)
    vr_str = _vr_indicator(value_ratio)

    zoning_parts = []
    if zoning:
        zoning_parts.append(zoning + " zoning")
    if far_remaining is not None:
        zoning_parts.append(f"{far_remaining:.1f} FAR remaining")
    if year_built:
        zoning_parts.append(f"Year built {int(year_built)}")
    zoning_line = " \u00b7 ".join(zoning_parts) if zoning_parts else None

    with cols[i % 3]:
        with st.container(border=True):
            st.markdown(f"**{address}**")
            loc_parts = [p for p in [borough, zip_code] if p]
            if loc_parts:
                st.caption(" ".join(loc_parts))

            detail_items = [s for s in [sqft_str, ppsf_str, beds_baths] if s]
            price_str = _fmt_price(price)
            if detail_items:
                st.markdown(f"**{price_str}**  \u00b7  " + "  \u00b7  ".join(detail_items))
            else:
                st.markdown(f"**{price_str}**")

            st.caption(f"AVM: {avm_str}  |  Value Ratio: {vr_str}")

            if zoning_line:
                st.caption(zoning_line)

            st.divider()

            btn_col, dom_col = st.columns([2, 1])
            with btn_col:
                btn_key = f"pipeline_{listing_id}_{i}"
                if st.button("Add to Pipeline", key=btn_key, use_container_width=True):
                    try:
                        add_listing_to_pipeline(listing)
                        st.toast("Added to pipeline!")
                        st.rerun()
                    except Exception as exc:
                        st.error(f"Failed to add: {exc}")

            with dom_col:
                if days_on_market is not None:
                    st.caption(f"{int(days_on_market)}d on market")
