"""Property Detail page — TES-19.

Navigated to via query param: ?property_id=<UUID>
"""

from __future__ import annotations

import pandas as pd
import pydeck as pdk
import streamlit as st
from db import (
    add_to_pipeline,
    load_deal_by_property,
    load_property_by_id,
    load_violations_by_property,
    update_deal_notes,
    update_deal_status,
)

st.set_page_config(page_title="Property Detail | NYC RE Tracker", page_icon="🏠", layout="wide")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
DEAL_ICONS = {
    "foreclosure": "🔴",
    "tax_lien": "🟠",
    "listing": "🔵",
    "off_market": "🟣",
}
DEAL_LABELS = {
    "foreclosure": "Foreclosure",
    "tax_lien": "Tax Lien",
    "listing": "Listing",
    "off_market": "Off Market",
}
PIPELINE_LABELS = {
    "watching": "👁 Watching",
    "analyzing": "🔍 Analyzing",
    "offer_made": "📝 Offer Made",
    "dead": "💀 Dead",
}
STAGES = ["watching", "analyzing", "offer_made", "dead"]

# ---------------------------------------------------------------------------
# Resolve property_id from query params
# ---------------------------------------------------------------------------
property_id = st.query_params.get("property_id")

if not property_id:
    st.error("No property selected.")
    st.page_link("pages/1_Deal_Feed.py", label="← Back to Deal Feed")
    st.stop()

# ---------------------------------------------------------------------------
# Load data
# ---------------------------------------------------------------------------
try:
    prop = load_property_by_id(property_id)
except Exception as e:
    st.error(f"Could not load property: {e}")
    st.page_link("pages/1_Deal_Feed.py", label="← Back to Deal Feed")
    st.stop()

if not prop:
    st.error(f"Property `{property_id}` not found.")
    st.page_link("pages/1_Deal_Feed.py", label="← Back to Deal Feed")
    st.stop()

try:
    deal = load_deal_by_property(property_id)
except Exception as e:
    st.warning(f"Could not load pipeline status: {e}")
    deal = None

# ---------------------------------------------------------------------------
# Back button
# ---------------------------------------------------------------------------
st.page_link("pages/1_Deal_Feed.py", label="← Back to Deal Feed")

st.divider()

# ---------------------------------------------------------------------------
# Header: address, borough, deal type badge, pipeline status
# ---------------------------------------------------------------------------
deal_type = prop.get("deal_type", "")
icon = DEAL_ICONS.get(deal_type, "⚪")
deal_label = DEAL_LABELS.get(deal_type, deal_type.replace("_", " ").title())
borough = prop.get("borough", "")
address = prop.get("address", "Unknown address")
pipeline_status = deal.get("status") if deal else None

header_col, badge_col = st.columns([3, 1])
with header_col:
    st.title(address)
    st.caption(borough)
with badge_col:
    st.markdown(f"**{icon} {deal_label}**")
    if pipeline_status:
        st.markdown(f"**{PIPELINE_LABELS.get(pipeline_status, pipeline_status)}**")

# ---------------------------------------------------------------------------
# Key metrics row
# ---------------------------------------------------------------------------
price = prop.get("price")
sqft = prop.get("sqft")
price_per_sqft = prop.get("price_per_sqft") or (
    round(price / sqft, 0) if price and sqft else None
)
beds = prop.get("bedrooms")
baths = prop.get("bathrooms")
year_built = prop.get("year_built")

m1, m2, m3, m4, m5, m6 = st.columns(6)
m1.metric("Price", f"${price:,.0f}" if price else "—")
m2.metric("Price/sqft", f"${price_per_sqft:,.0f}" if price_per_sqft else "—")
m3.metric("Sqft", f"{sqft:,}" if sqft else "—")
m4.metric("Beds", int(beds) if beds is not None else "—")
m5.metric("Baths", f"{baths:g}" if baths is not None else "—")
m6.metric("Year Built", year_built if year_built else "—")

st.divider()

# ---------------------------------------------------------------------------
# Map + Notes side by side
# ---------------------------------------------------------------------------
map_col, notes_col = st.columns([3, 2])

with map_col:
    lat = prop.get("lat")
    lng = prop.get("lng")
    if lat and lng:
        df = pd.DataFrame([{"lat": float(lat), "lng": float(lng), "address": address}])
        layer = pdk.Layer(
            "ScatterplotLayer",
            data=df,
            get_position=["lng", "lat"],
            get_fill_color=[220, 38, 38, 220],
            get_radius=80,
            radius_min_pixels=8,
            radius_max_pixels=24,
            pickable=True,
        )
        view = pdk.ViewState(latitude=float(lat), longitude=float(lng), zoom=15, pitch=0)
        st.pydeck_chart(
            pdk.Deck(
                layers=[layer],
                initial_view_state=view,
                tooltip={"html": "<b>{address}</b>", "style": {"color": "white"}},
                map_style="mapbox://styles/mapbox/dark-v10",
            )
        )
    else:
        st.info("No coordinates available for this property.")

with notes_col:
    st.subheader("Notes")
    current_notes = deal.get("notes") or "" if deal else ""
    new_notes = st.text_area(
        "Notes",
        value=current_notes,
        height=180,
        label_visibility="collapsed",
        placeholder="Add your analysis notes here…",
    )
    if deal:
        if st.button("Save Notes", use_container_width=True):
            try:
                update_deal_notes(deal["id"], new_notes)
                st.success("Notes saved.")
            except Exception as e:
                st.error(f"Failed to save notes: {e}")
        st.divider()
        st.caption("**Pipeline Stage**")
        new_status = st.selectbox(
            "Stage",
            options=STAGES,
            index=STAGES.index(pipeline_status) if pipeline_status in STAGES else 0,
            format_func=lambda s: PIPELINE_LABELS.get(s, s),
            label_visibility="collapsed",
        )
        if new_status != pipeline_status:
            update_deal_status(deal["id"], new_status)
            st.rerun()
    else:
        if st.button("+ Add to Pipeline", use_container_width=True):
            try:
                add_to_pipeline(property_id, "watching")
                st.rerun()
            except Exception as e:
                st.error(f"Failed to add to pipeline: {e}")

st.divider()

# ---------------------------------------------------------------------------
# Violations
# ---------------------------------------------------------------------------
try:
    violations = load_violations_by_property(property_id)
except Exception as e:
    violations = []
    st.warning(f"Could not load violations: {e}")

hpd_viols = [v for v in violations if v.get("source") == "hpd"]
dob_viols = [v for v in violations if v.get("source") == "dob"]
total_count = len(violations)

expander_label = f"⚠️ Violations ({total_count})" if total_count else "⚠️ Violations"
with st.expander(expander_label, expanded=total_count > 0):
    if not violations:
        st.info("No violations on record.")
    else:
        hpd_tab, dob_tab = st.tabs([
            f"HPD ({len(hpd_viols)})",
            f"DOB ({len(dob_viols)})",
        ])

        with hpd_tab:
            if not hpd_viols:
                st.info("No HPD violations.")
            else:
                for v in hpd_viols:
                    with st.container(border=True):
                        c1, c2, c3 = st.columns([1, 2, 1])
                        vtype = v.get("violation_type") or "?"
                        c1.markdown(f"**Class {vtype}**")
                        status = v.get("status") or ""
                        c2.caption(f"{'🔴' if status == 'Open' else '🟢'} {status}")
                        c3.caption(v.get("issued_date") or "")
                        if v.get("description"):
                            st.caption(v["description"])

        with dob_tab:
            if not dob_viols:
                st.info("No DOB violations.")
            else:
                for v in dob_viols:
                    with st.container(border=True):
                        vtype = v.get("violation_type") or ""
                        status = v.get("status") or ""
                        c1, c2 = st.columns([3, 1])
                        if vtype:
                            c1.markdown(f"**{vtype}**")
                        c2.caption(f"{'🔴' if status == 'Open' else '🟢'} {status}")
                        if v.get("issued_date"):
                            st.caption(f"Issued: {v['issued_date']}")
                        if v.get("description"):
                            st.caption(v["description"])

# ---------------------------------------------------------------------------
# Deal Calculator placeholder (TES-21)
# ---------------------------------------------------------------------------
with st.expander("🧮 Quick Deal Calculator", expanded=False):
    st.info("Deal calculator will appear here. *(Coming in TES-21)*")
