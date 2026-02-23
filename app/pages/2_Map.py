"""Map View — properties plotted on an interactive NYC map (TES-11, TES-22)."""

from __future__ import annotations

from collections import Counter

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
# Color map (RGBA) by deal type
# ---------------------------------------------------------------------------
DEAL_COLORS: dict[str, list[int]] = {
    "foreclosure": [220, 38, 38, 200],
    "tax_lien": [234, 88, 12, 200],
    "listing": [37, 99, 235, 200],
    "off_market": [124, 58, 237, 200],
}
DEAL_LABELS = {
    "foreclosure": "Foreclosure",
    "tax_lien": "Tax Lien",
    "listing": "Listing",
    "off_market": "Off Market",
}

# ---------------------------------------------------------------------------
# Sidebar
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

    st.divider()
    st.header("View")
    cluster_mode = st.toggle("Cluster nearby pins", value=False)
    if cluster_mode:
        cluster_precision = st.slider(
            "Cluster radius",
            min_value=1, max_value=4, value=2,
            help="Higher = tighter clusters (zoom in). Lower = broader grouping.",
        )

# ---------------------------------------------------------------------------
# Filter and build base rows
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
    rows.append({
        "lat": float(lat),
        "lng": float(lng),
        "address": p.get("address", ""),
        "borough": p.get("borough", ""),
        "deal_type": deal_type,
        "deal_label": DEAL_LABELS.get(deal_type, deal_type),
        "price": p.get("price"),
        "pipeline": tracked.get(p["id"], ""),
        "color": DEAL_COLORS.get(deal_type, [100, 100, 100, 180]),
    })

if not rows:
    if not properties:
        st.info("No properties found. Run `python data/ingest_nyc_open_data.py` to populate.")
    elif all(p.get("lat") is None for p in properties):
        st.warning("Properties loaded but none have coordinates. Re-run ingestion without `--no-geocode`.")
    else:
        st.warning("No properties match your current filters.")
    st.stop()


# ---------------------------------------------------------------------------
# Clustering helpers
# ---------------------------------------------------------------------------

def build_clusters(rows: list[dict], precision: int) -> tuple[list[dict], list[dict]]:
    """
    Group rows into grid cells by rounding lat/lng to `precision` decimals.
    Returns (cluster_rows, label_rows) for ScatterplotLayer + TextLayer.
    """
    buckets: dict[tuple, list[dict]] = {}
    for row in rows:
        key = (round(row["lat"], precision), round(row["lng"], precision))
        buckets.setdefault(key, []).append(row)

    cluster_rows, label_rows = [], []
    for members in buckets.values():
        count = len(members)
        avg_lat = sum(r["lat"] for r in members) / count
        avg_lng = sum(r["lng"] for r in members) / count

        # Dominant deal type → color
        dominant = Counter(r["deal_type"] for r in members).most_common(1)[0][0]
        color = DEAL_COLORS.get(dominant, [100, 100, 100, 200])
        # Slight transparency boost for clusters
        color = color[:3] + [220]

        # Summary tooltip
        type_counts = Counter(r["deal_type"] for r in members)
        summary = " · ".join(
            f"{DEAL_LABELS.get(dt, dt)}: {n}" for dt, n in type_counts.most_common()
        )

        cluster_rows.append({
            "lat": avg_lat,
            "lng": avg_lng,
            "count": count,
            "color": color,
            "summary": summary,
        })
        label_rows.append({
            "lat": avg_lat,
            "lng": avg_lng,
            "label": str(count) if count > 1 else "",
        })

    return cluster_rows, label_rows


# ---------------------------------------------------------------------------
# Build layers
# ---------------------------------------------------------------------------

if cluster_mode:
    cluster_rows, label_rows = build_clusters(rows, cluster_precision)
    df_clusters = pd.DataFrame(cluster_rows)
    df_labels = pd.DataFrame(label_rows)

    max_count = df_clusters["count"].max() if len(df_clusters) else 1

    scatter_layer = pdk.Layer(
        "ScatterplotLayer",
        data=df_clusters,
        get_position=["lng", "lat"],
        get_fill_color="color",
        get_radius="count",
        radius_scale=120,
        radius_min_pixels=10,
        radius_max_pixels=60,
        pickable=True,
    )

    text_layer = pdk.Layer(
        "TextLayer",
        data=df_labels,
        get_position=["lng", "lat"],
        get_text="label",
        get_size=14,
        get_color=[255, 255, 255, 230],
        get_alignment_baseline="'center'",
        get_text_anchor="'middle'",
    )

    tooltip = {
        "html": "<b>{count} properties</b><br/>{summary}",
        "style": {
            "backgroundColor": "#1e293b",
            "color": "white",
            "fontSize": "13px",
            "padding": "8px",
            "borderRadius": "4px",
        },
    }
    layers = [scatter_layer, text_layer]

else:
    df = pd.DataFrame([{
        **r,
        "price_fmt": f"${r['price']:,.0f}" if r.get("price") else "Unknown",
        "pipeline_fmt": r["pipeline"].replace("_", " ").title() or "—",
    } for r in rows])

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

    tooltip = {
        "html": (
            "<b>{address}</b><br/>"
            "{deal_label} · {borough}<br/>"
            "Price: {price_fmt}<br/>"
            "Pipeline: {pipeline_fmt}"
        ),
        "style": {
            "backgroundColor": "#1e293b",
            "color": "white",
            "fontSize": "13px",
            "padding": "8px",
            "borderRadius": "4px",
        },
    }
    layers = [scatter_layer]

# ---------------------------------------------------------------------------
# Render map
# ---------------------------------------------------------------------------
view_state = pdk.ViewState(
    latitude=40.7128,
    longitude=-74.0060,
    zoom=10,
    pitch=0,
)

st.pydeck_chart(
    pdk.Deck(
        layers=layers,
        initial_view_state=view_state,
        tooltip=tooltip,
        map_style="mapbox://styles/mapbox/dark-v10",
    )
)

# ---------------------------------------------------------------------------
# Legend + summary
# ---------------------------------------------------------------------------
st.divider()

mapped = len(rows)
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
    if cluster_mode:
        cluster_count = len(build_clusters(rows, cluster_precision)[0])
        st.caption(f"**{mapped}** properties in **{cluster_count}** clusters")
    else:
        st.caption(f"**{mapped}** pins shown")
    if no_coords:
        st.caption(f"*{no_coords} properties skipped (no coordinates)*")
