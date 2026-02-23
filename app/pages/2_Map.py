"""Map View — properties plotted on an interactive NYC map (TES-11, TES-22, TES-23)."""

from __future__ import annotations

from collections import Counter

import pandas as pd
import pydeck as pdk
import streamlit as st
from db import add_to_pipeline, load_deals, load_properties, load_violation_counts

st.set_page_config(page_title="Map | NYC RE Tracker", page_icon="🗺", layout="wide")

st.title("Map")
st.caption("Geographic view of all tracked properties")

# ---------------------------------------------------------------------------
# Load data
# ---------------------------------------------------------------------------
try:
    properties = load_properties()
    deals = load_deals()
    violation_counts = load_violation_counts()
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
DEAL_ICONS = {
    "foreclosure": "🔴",
    "tax_lien": "🟠",
    "listing": "🔵",
    "off_market": "🟣",
}
PIPELINE_LABELS = {
    "watching": "👁 Watching",
    "analyzing": "🔍 Analyzing",
    "offer_made": "📝 Offer Made",
    "dead": "💀 Dead",
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
            help="Higher = tighter clusters. Lower = broader grouping.",
        )

# ---------------------------------------------------------------------------
# Filter and build base rows
# ---------------------------------------------------------------------------
deal_type_values = {DEAL_TYPE_OPTIONS[k] for k in deal_type_sel}

rows = []
prop_by_index: dict[int, dict] = {}  # row index → full property dict for click lookup

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
    idx = len(rows)
    prop_by_index[idx] = p
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
    buckets: dict[tuple, list[dict]] = {}
    for row in rows:
        key = (round(row["lat"], precision), round(row["lng"], precision))
        buckets.setdefault(key, []).append(row)

    cluster_rows, label_rows = [], []
    for members in buckets.values():
        count = len(members)
        avg_lat = sum(r["lat"] for r in members) / count
        avg_lng = sum(r["lng"] for r in members) / count
        dominant = Counter(r["deal_type"] for r in members).most_common(1)[0][0]
        color = DEAL_COLORS.get(dominant, [100, 100, 100, 200])[:3] + [220]
        type_counts = Counter(r["deal_type"] for r in members)
        summary = " · ".join(
            f"{DEAL_LABELS.get(dt, dt)}: {n}" for dt, n in type_counts.most_common()
        )
        cluster_rows.append({"lat": avg_lat, "lng": avg_lng, "count": count,
                              "color": color, "summary": summary})
        label_rows.append({"lat": avg_lat, "lng": avg_lng,
                           "label": str(count) if count > 1 else ""})

    return cluster_rows, label_rows


# ---------------------------------------------------------------------------
# Build layers + render map
# ---------------------------------------------------------------------------
selected_prop = None

if cluster_mode:
    cluster_rows, label_rows = build_clusters(rows, cluster_precision)
    df_clusters = pd.DataFrame(cluster_rows)
    df_labels = pd.DataFrame(label_rows)

    layers = [
        pdk.Layer("ScatterplotLayer", data=df_clusters, get_position=["lng", "lat"],
                  get_fill_color="color", get_radius="count", radius_scale=120,
                  radius_min_pixels=10, radius_max_pixels=60, pickable=True),
        pdk.Layer("TextLayer", data=df_labels, get_position=["lng", "lat"],
                  get_text="label", get_size=14, get_color=[255, 255, 255, 230],
                  get_alignment_baseline="'center'", get_text_anchor="'middle'"),
    ]
    tooltip = {
        "html": "<b>{count} properties</b><br/>{summary}",
        "style": {"backgroundColor": "#1e293b", "color": "white",
                  "fontSize": "13px", "padding": "8px", "borderRadius": "4px"},
    }
    st.pydeck_chart(pdk.Deck(layers=layers,
        initial_view_state=pdk.ViewState(latitude=40.7128, longitude=-74.0060, zoom=10),
        tooltip=tooltip, map_style="mapbox://styles/mapbox/dark-v10"))

else:
    df = pd.DataFrame([{
        **r,
        "price_fmt": f"${r['price']:,.0f}" if r.get("price") else "Unknown",
        "pipeline_fmt": r["pipeline"].replace("_", " ").title() or "—",
    } for r in rows])

    scatter_layer = pdk.Layer(
        "ScatterplotLayer",
        id="pins",
        data=df,
        get_position=["lng", "lat"],
        get_fill_color="color",
        get_radius=80,
        radius_min_pixels=6,
        radius_max_pixels=20,
        pickable=True,
        auto_highlight=True,
    )
    tooltip = {
        "html": "<b>{address}</b><br/>{deal_label} · {borough}<br/>Price: {price_fmt}<br/>Pipeline: {pipeline_fmt}",
        "style": {"backgroundColor": "#1e293b", "color": "white",
                  "fontSize": "13px", "padding": "8px", "borderRadius": "4px"},
    }
    deck = pdk.Deck(
        layers=[scatter_layer],
        initial_view_state=pdk.ViewState(latitude=40.7128, longitude=-74.0060, zoom=10),
        tooltip=tooltip,
        map_style="mapbox://styles/mapbox/dark-v10",
    )

    try:
        chart_event = st.pydeck_chart(deck, on_select="rerun", selection_mode="single-object")
        selected_indices = (chart_event.selection or {}).get("indices", {}).get("pins", [])
        selected_prop = prop_by_index.get(selected_indices[0]) if selected_indices else None
    except TypeError:
        # Fallback for older Streamlit without on_select support
        st.pydeck_chart(deck)


# ---------------------------------------------------------------------------
# Selected pin detail panel
# ---------------------------------------------------------------------------

if selected_prop:
    st.divider()
    pid = selected_prop["id"]
    deal_type = selected_prop.get("deal_type", "")
    pipeline_status = tracked.get(pid)
    vcount = violation_counts.get(pid, 0)
    price = selected_prop.get("price")

    with st.container(border=True):
        col_info, col_actions = st.columns([3, 1])

        with col_info:
            icon = DEAL_ICONS.get(deal_type, "⚪")
            label = DEAL_LABELS.get(deal_type, deal_type)
            st.markdown(f"### {selected_prop.get('address', '')}")
            st.caption(f"{icon} {label} · {selected_prop.get('borough', '')}")

            m1, m2, m3 = st.columns(3)
            m1.metric("Price", f"${price:,.0f}" if price else "—")
            m2.metric("Violations", vcount if vcount else "0")
            m3.metric(
                "Pipeline",
                PIPELINE_LABELS.get(pipeline_status, "Not tracked") if pipeline_status else "Not tracked",
            )

        with col_actions:
            st.page_link(
                "pages/5_Property_Detail.py",
                label="View Full Details →",
            )
            if pipeline_status:
                st.success(PIPELINE_LABELS.get(pipeline_status, pipeline_status))
            else:
                if st.button("+ Watch", use_container_width=True, key=f"watch_{pid}"):
                    try:
                        add_to_pipeline(pid, "watching")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Failed: {e}")


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
        st.caption(f"**{mapped}** pins shown · click a pin for details")
    if no_coords:
        st.caption(f"*{no_coords} properties skipped (no coordinates)*")
