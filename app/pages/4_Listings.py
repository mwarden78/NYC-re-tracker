"""Listings feed — active NYC for-sale listings ranked by AVM value ratio (TES-76)."""

from __future__ import annotations

import streamlit as st
from db import add_listing_to_pipeline, load_listings
from sidebar import render_more_section

st.set_page_config(page_title="Listings | NYC RE Tracker", page_icon="🏷", layout="wide")
render_more_section()

st.title("Listings")
st.caption("Active NYC for-sale listings — ranked by AI-estimated value vs. asking price")

# ---------------------------------------------------------------------------
# Sidebar filters
# ---------------------------------------------------------------------------
with st.sidebar:
    st.header("Filters")

    BOROUGHS = ["Manhattan", "Brooklyn", "Queens", "Bronx", "Staten Island"]
    borough_sel = st.multiselect("Borough", BOROUGHS, placeholder="All boroughs")

    PROP_TYPES = [
        "Single Family", "Multi Family", "Condo", "Co-op", "Townhouse", "Land",
    ]
    prop_type_sel = st.multiselect("Property Type", PROP_TYPES, placeholder="All types")

    price_min, price_max = st.slider(
        "Price range",
        min_value=50_000,
        max_value=10_000_000,
        value=(50_000, 5_000_000),
        step=50_000,
        format="$%d",
    )

    min_value_ratio = st.slider(
        "Min value ratio (AVM / asking)",
        min_value=1.0,
        max_value=2.0,
        value=1.0,
        step=0.05,
        help="Only show listings where the model estimates value ≥ X× asking price. "
             "Requires AVM scoring to be run first.",
    )

    min_sqft = st.number_input(
        "Min sqft", min_value=0, value=0, step=100,
        help="Minimum gross square footage",
    )

# ---------------------------------------------------------------------------
# Load data
# ---------------------------------------------------------------------------
try:
    listings = load_listings(
        boroughs=borough_sel or None,
        price_min=price_min,
        price_max=price_max,
        prop_types=[t.lower().replace(" ", "_") for t in prop_type_sel] if prop_type_sel else None,
        min_value_ratio=min_value_ratio if min_value_ratio > 1.0 else None,
        min_sqft=min_sqft if min_sqft > 0 else None,
    )
except Exception as e:
    st.error(f"Could not load listings from Supabase. Check your `.env` file. ({e})")
    st.stop()

# ---------------------------------------------------------------------------
# Empty state
# ---------------------------------------------------------------------------
if not listings:
    st.info(
        "No listings found. "
        "Run the RentCast ingest script to populate this feed once TES-74 is complete:\n\n"
        "```bash\npython data/ingest_rentcast_listings.py\n```",
        icon="ℹ️",
    )
    st.stop()

# ---------------------------------------------------------------------------
# Header row
# ---------------------------------------------------------------------------
scored = sum(1 for l in listings if l.get("value_ratio") is not None)
st.markdown(
    f"**{len(listings):,} listings** · {scored:,} AVM-scored"
    + (f" · {len(listings) - scored:,} pending score" if scored < len(listings) else "")
)

# ---------------------------------------------------------------------------
# Listing cards
# ---------------------------------------------------------------------------
_VALUE_RATIO_CAP = 5.0  # ratios above this are almost certainly data artefacts

def _value_ratio_badge(vr: float | None) -> str:
    """Return a coloured emoji badge based on value ratio."""
    if vr is None:
        return "⚪ Not scored"
    display = f"{min(vr, _VALUE_RATIO_CAP):.2f}×" + ("+" if vr > _VALUE_RATIO_CAP else "")
    if vr >= 1.15:
        return f"🟢 {display}"
    if vr >= 1.05:
        return f"🟡 {display}"
    return f"⚪ {display}"


def _fmt_price(val: int | None) -> str:
    if val is None:
        return "—"
    if val >= 1_000_000:
        return f"${val / 1_000_000:.2f}M"
    return f"${val:,}"


for listing in listings:
    address = listing.get("address", "Unknown address")
    borough = listing.get("borough") or ""
    zip_code = listing.get("zip_code") or ""
    price = listing.get("price")
    sqft = listing.get("sqft")
    ppsf = listing.get("price_per_sqft")
    beds = listing.get("beds")
    baths = listing.get("baths")
    prop_type = listing.get("property_type") or ""
    days_on_market = listing.get("days_on_market")
    predicted = listing.get("predicted_value")
    value_ratio = listing.get("value_ratio")
    zonedist = listing.get("zonedist1") or ""
    far_rem = listing.get("far_remaining")
    listing_id = listing.get("id", "")

    with st.container(border=True):
        col_main, col_action = st.columns([5, 1])

        with col_main:
            # Row 1: address + location
            loc_parts = [p for p in [borough, zip_code] if p]
            st.markdown(f"**{address}**" + (f"  ·  {', '.join(loc_parts)}" if loc_parts else ""))

            # Row 2: price + size
            price_str = _fmt_price(price)
            size_parts: list[str] = []
            if sqft:
                size_parts.append(f"{sqft:,} sqft")
            if ppsf:
                size_parts.append(f"${ppsf:,.0f}/sqft")
            if beds is not None:
                size_parts.append(f"{beds:.0f}bd")
            if baths is not None:
                size_parts.append(f"{baths:.0f}ba")
            if prop_type:
                size_parts.append(prop_type.replace("_", " ").title())
            details = "  ·  ".join(size_parts)
            st.markdown(f"**{price_str}** asking" + (f"  ·  {details}" if details else ""))

            # Row 3: AVM score
            badge = _value_ratio_badge(value_ratio)
            avm_str = f"AVM: {_fmt_price(predicted)}" if predicted else "AVM: pending"
            zoning_parts: list[str] = []
            if zonedist:
                zoning_parts.append(f"{zonedist} zoning")
            if far_rem is not None:
                zoning_parts.append(f"{far_rem:.2f} FAR remaining")
            if days_on_market is not None:
                zoning_parts.append(f"{days_on_market}d on market")
            zoning_str = "  ·  ".join(zoning_parts)
            st.markdown(
                f"{badge}  ·  {avm_str}"
                + (f"  ·  {zoning_str}" if zoning_str else "")
            )

        with col_action:
            st.markdown("")  # vertical spacer
            btn_key = f"pipeline_{listing_id}"
            if st.button("＋ Pipeline", key=btn_key, use_container_width=True,
                         help="Add this listing to your deal pipeline as Watching"):
                try:
                    add_listing_to_pipeline(listing)
                    st.toast(f"Added {address} to pipeline!", icon="✅")
                    st.rerun()
                except Exception as e:
                    st.error(f"Could not add to pipeline: {e}")
