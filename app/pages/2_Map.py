"""Map View — properties plotted on an interactive NYC map (TES-11)."""

from __future__ import annotations

import pandas as pd
import pydeck as pdk
import streamlit as st
from db import load_deals, load_properties

st.set_page_config(page_title="Map | NYC RE Tracker", page_icon="🗺", layout="wide")

st.title("Map")
st.caption("Geographic view of all tracked properties")

# ---------------------------------------------------------------------------
# Load data
# ---------------------------------------------------------------------------
try:
    properties = load_properties()
    deals = load_deals()
except Exception as e:
    st.error(f"Could not load data from Supabase. Check your `.env` file. ({e})")
    st.stop()

tracked: dict[str, str] = {d["property_id"]: d["status"] for d in deals}

# ---------------------------------------------------------------------------
# Sidebar filters
# ---------------------------------------------------------------------------
with st.sidebar:
    st.header("Filters")

    BOROUGHS = ["Brooklyn", "Queens", "Manhattan", "Bronx", "Staten Island"]
    borough_sel = st.multiselect("Borough", BOROUGHS, placeholder="All boroughs")

    DEAL_TYPE_OPTIONS = {
        "Foreclosure": "foreclosure",
        "Tax Lien": "tax_lien",
        "Listing": "listing",
        "Off Market": "off_market",
    }
    deal_type_sel = st.multiselect(
        "Deal Type", list(DEAL_TYPE_OPTIONS.keys()), placeholder="All types"
    )

    show_tracked_only = st.checkbox("Show tracked properties only", value=False)

# ---------------------------------------------------------------------------
# Color map (RGBA) by deal type
# ---------------------------------------------------------------------------
DEAL_COLORS: dict[str, list[int]] = {
    "foreclosure": [220, 38, 38, 200],    # red
    "tax_lien": [234, 88, 12, 200],       # orange
    "listing": [37, 99, 235, 200],        # blue
    "off_market": [124, 58, 237, 200],    # purple
}
DEAL_LABELS = {
    "foreclosure": "Foreclosure",
    "tax_lien": "Tax Lien",
    "listing": "Listing",
    "off_market": "Off Market",
}

# ---------------------------------------------------------------------------
# Filter and build DataFrame
# ---------------------------------------------------------------------------
deal_type_values = {DEAL_TYPE_OPTIONS[k] for k in deal_type_sel}

rows = []
for p in properties:
    lat = p.get("lat")
    lng = p.get("lng")
    if lat is None or lng is None:
        continue

    if borough_sel and p.get("borough") not in borough_sel:
        continue
    if deal_type_values and p.get("deal_type") not in deal_type_values:
        continue
    if show_tracked_only and p["id"] not in tracked:
        continue

    deal_type = p.get("deal_type", "")
    color = DEAL_COLORS.get(deal_type, [100, 100, 100, 180])
    price = p.get("price")

    rows.append({
        "lat": float(lat),
        "lng": float(lng),
        "address": p.get("address", ""),
        "borough": p.get("borough", ""),
        "deal_type": DEAL_LABELS.get(deal_type, deal_type),
        "price": f"${price:,.0f}" if price else "Unknown",
        "pipeline": tracked.get(p["id"], "").replace("_", " ").title() or "—",
        "color": color,
    })

# ---------------------------------------------------------------------------
# Render map
# ---------------------------------------------------------------------------
if not rows:
    if not properties:
        st.info(
            "No properties found. Run `python data/ingest_nyc_open_data.py` to populate."
        )
    elif all(p.get("lat") is None for p in properties):
        st.warning(
            "Properties loaded but none have coordinates. "
            "Re-run ingestion without `--no-geocode` to add lat/lng."
        )
    else:
        st.warning("No properties match your current filters.")
    st.stop()

df = pd.DataFrame(rows)

scatter_layer = pdk.Layer(
    "ScatterplotLayer",
    data=df,
    get_position=["lng", "lat"],
    get_fill_color="color",
    get_radius=80,
    radius_min_pixels=6,
    radius_max_pixels=20,
    pickable=True,
)

view_state = pdk.ViewState(
    latitude=40.7128,
    longitude=-74.0060,
    zoom=10,
    pitch=0,
)

tooltip = {
    "html": (
        "<b>{address}</b><br/>"
        "{deal_type} · {borough}<br/>"
        "Price: {price}<br/>"
        "Pipeline: {pipeline}"
    ),
    "style": {
        "backgroundColor": "#1e293b",
        "color": "white",
        "fontSize": "13px",
        "padding": "8px",
        "borderRadius": "4px",
    },
}

st.pydeck_chart(
    pdk.Deck(
        layers=[scatter_layer],
        initial_view_state=view_state,
        tooltip=tooltip,
        map_style="mapbox://styles/mapbox/dark-v10",
    )
)

# ---------------------------------------------------------------------------
# Legend + summary
# ---------------------------------------------------------------------------
st.divider()

mapped = len(df)
total = len(properties)
no_coords = total - sum(1 for p in properties if p.get("lat") and p.get("lng"))

col_legend, col_stats = st.columns([2, 1])

with col_legend:
    st.caption("**Legend**")
    legend_cols = st.columns(4)
    for i, (key, label) in enumerate(DEAL_LABELS.items()):
        r, g, b, _ = DEAL_COLORS[key]
        hex_color = f"#{r:02x}{g:02x}{b:02x}"
        legend_cols[i].markdown(
            f"<span style='color:{hex_color};font-size:18px'>●</span> {label}",
            unsafe_allow_html=True,
        )

with col_stats:
    st.caption(f"**{mapped}** pins shown")
    if no_coords:
        st.caption(f"*{no_coords} properties skipped (no coordinates)*")
