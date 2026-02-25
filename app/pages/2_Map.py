"""Map View — properties plotted on an interactive NYC map (TES-11, TES-22, TES-23, TES-24, TES-27, TES-46)."""

from __future__ import annotations

import math
from collections import Counter

import pandas as pd
import pydeck as pdk
import requests
import streamlit as st
from db import add_to_pipeline, load_deals, load_last_sales, load_properties, load_violation_counts

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

try:
    last_sales = load_last_sales()
except Exception:
    last_sales = {}

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
# Geo helpers
# ---------------------------------------------------------------------------

def haversine_miles(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Return great-circle distance in miles between two lat/lng points."""
    R = 3958.8  # Earth radius in miles
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def geocode_nyc(address: str) -> tuple[float, float] | None:
    """Geocode an NYC address using the NYC GeoSearch API. Returns (lat, lng) or None."""
    try:
        resp = requests.get(
            "https://geosearch.planninglabs.nyc/v2/search",
            params={"text": address, "size": 1},
            timeout=5,
        )
        resp.raise_for_status()
        features = resp.json().get("features", [])
        if not features:
            return None
        coords = features[0]["geometry"]["coordinates"]  # [lng, lat]
        return float(coords[1]), float(coords[0])
    except Exception:
        return None


def circle_polygon(lat: float, lng: float, radius_miles: float, points: int = 64) -> list[list[float]]:
    """Return a list of [lng, lat] pairs forming a circle polygon."""
    lat_deg = radius_miles / 69.0
    lng_deg = radius_miles / (69.0 * math.cos(math.radians(lat)))
    ring = []
    for i in range(points + 1):
        angle = 2 * math.pi * i / points
        ring.append([lng + lng_deg * math.cos(angle), lat + lat_deg * math.sin(angle)])
    return ring


# ---------------------------------------------------------------------------
# MTA subway line colors (standard palette)
# ---------------------------------------------------------------------------
_MTA_LINE_COLORS: dict[str, list[int]] = {
    "A": [40, 80, 173, 210], "C": [40, 80, 173, 210], "E": [40, 80, 173, 210],
    "B": [255, 99, 25, 210], "D": [255, 99, 25, 210], "F": [255, 99, 25, 210], "M": [255, 99, 25, 210],
    "G": [108, 190, 69, 210],
    "J": [153, 102, 51, 210], "Z": [153, 102, 51, 210],
    "L": [167, 169, 172, 210],
    "N": [252, 204, 10, 210], "Q": [252, 204, 10, 210], "R": [252, 204, 10, 210], "W": [252, 204, 10, 210],
    "1": [238, 53, 46, 210], "2": [238, 53, 46, 210], "3": [238, 53, 46, 210],
    "4": [0, 147, 60, 210], "5": [0, 147, 60, 210], "6": [0, 147, 60, 210],
    "7": [185, 51, 173, 210],
    "S": [128, 129, 131, 210],
}
_DEFAULT_TRANSIT_COLOR = [128, 129, 131, 210]


@st.cache_data(ttl=86400, show_spinner="Loading subway lines…")
def _load_subway_lines() -> list[dict]:
    """Fetch NYC subway lines GeoJSON from NYC Open Data (cached 24 h)."""
    url = "https://data.cityofnewyork.us/api/geospatial/s7zz-qmyz?method=export&type=GeoJSON"
    resp = requests.get(url, timeout=15)
    resp.raise_for_status()
    geojson = resp.json()

    paths = []
    for feature in geojson.get("features", []):
        props = feature.get("properties", {})
        route = (props.get("rt_symbol") or "").strip()
        color = _MTA_LINE_COLORS.get(route, _DEFAULT_TRANSIT_COLOR)
        geom = feature.get("geometry", {})
        geom_type = geom.get("type")
        coords = geom.get("coordinates", [])

        if geom_type == "LineString":
            paths.append({"path": coords, "color": color, "route": route})
        elif geom_type == "MultiLineString":
            for segment in coords:
                paths.append({"path": segment, "color": color, "route": route})

    return paths


@st.cache_data(ttl=86400, show_spinner="Loading subway stations…")
def _load_subway_stations() -> list[dict]:
    """Fetch NYC subway stations from NYC Open Data (cached 24 h)."""
    url = "https://data.cityofnewyork.us/resource/arq3-7z49.json?$limit=500"
    resp = requests.get(url, timeout=15)
    resp.raise_for_status()
    data = resp.json()

    stations = []
    for s in data:
        geom = s.get("the_geom")
        if isinstance(geom, dict) and geom.get("type") == "Point":
            lng, lat = geom["coordinates"]
            stations.append({
                "lat": float(lat),
                "lng": float(lng),
                "name": s.get("name", ""),
                "line": s.get("line", ""),
            })
    return stations


def _build_subway_layers() -> list[pdk.Layer]:
    """Return PathLayer (lines) + ScatterplotLayer (stations), or [] on failure."""
    try:
        subway_paths = _load_subway_lines()
        layers: list[pdk.Layer] = []

        if subway_paths:
            layers.append(
                pdk.Layer(
                    "PathLayer",
                    data=pd.DataFrame(subway_paths),
                    get_path="path",
                    get_color="color",
                    get_width=12,
                    width_min_pixels=2,
                    width_max_pixels=6,
                    pickable=False,
                    joint_rounded=True,
                    cap_rounded=True,
                )
            )

        stations = _load_subway_stations()
        if stations:
            layers.append(
                pdk.Layer(
                    "ScatterplotLayer",
                    data=pd.DataFrame(stations),
                    get_position=["lng", "lat"],
                    get_fill_color=[255, 255, 255, 200],
                    get_line_color=[80, 80, 80, 255],
                    stroked=True,
                    line_width_min_pixels=1,
                    get_radius=40,
                    radius_min_pixels=3,
                    radius_max_pixels=8,
                    pickable=False,
                )
            )

        return layers
    except Exception as exc:
        st.sidebar.warning(f"Subway data unavailable: {exc}")
        return []


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
    st.header("Radius Search")
    radius_address = st.text_input(
        "Center address",
        placeholder="e.g. 123 Main St, Brooklyn",
        help="Enter any NYC address to search within a radius",
    )
    radius_miles = st.slider(
        "Radius (miles)",
        min_value=0.25, max_value=5.0, value=1.0, step=0.25,
        disabled=not radius_address,
    )
    search_clicked = st.button("Search", disabled=not radius_address, use_container_width=True)

    st.divider()
    st.header("View")
    MAP_STYLES = {
        "Dark": "https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json",
        "Light": "https://basemaps.cartocdn.com/gl/positron-gl-style/style.json",
        "Voyager": "https://basemaps.cartocdn.com/gl/voyager-gl-style/style.json",
    }
    map_style_name = st.radio("Map style", list(MAP_STYLES.keys()), horizontal=True)
    map_style = MAP_STYLES[map_style_name]

    cluster_mode = st.toggle("Cluster nearby pins", value=False)
    if cluster_mode:
        cluster_precision = st.slider(
            "Cluster radius",
            min_value=1, max_value=4, value=2,
            help="Higher = tighter clusters. Lower = broader grouping.",
        )
    show_subway = st.toggle("Show subway layer", value=False,
                            help="Overlay MTA subway lines and stations")

    st.divider()
    st.header("Map Bounds Filter")
    filter_by_bounds = st.toggle(
        "Filter by map view",
        value=False,
        help="Show only properties within the lat/lng bounds below.",
    )
    if filter_by_bounds:
        st.caption("Adjust to match your current map view.")
        lat_range = st.slider(
            "Latitude",
            min_value=40.45, max_value=40.95,
            value=(40.55, 40.92),
            step=0.01,
            format="%.2f",
        )
        lng_range = st.slider(
            "Longitude",
            min_value=-74.35, max_value=-73.65,
            value=(-74.25, -73.70),
            step=0.01,
            format="%.2f",
        )
    else:
        lat_range = None
        lng_range = None

    show_heatmap = st.toggle("Show violation heatmap", value=False)
    if show_heatmap:
        heatmap_mode = st.radio(
            "Violations to show",
            options=["All violations", "Open only"],
            horizontal=True,
        )

# ---------------------------------------------------------------------------
# Radius search state — geocode on button click, persist in session_state
# ---------------------------------------------------------------------------
if "radius_center" not in st.session_state:
    st.session_state.radius_center = None  # (lat, lng) or None
if "radius_address_last" not in st.session_state:
    st.session_state.radius_address_last = ""

if search_clicked and radius_address:
    with st.spinner("Geocoding address…"):
        result = geocode_nyc(radius_address)
    if result:
        st.session_state.radius_center = result
        st.session_state.radius_address_last = radius_address
    else:
        st.sidebar.error("Address not found. Try a more specific NYC address.")
        st.session_state.radius_center = None

# Clear radius center if address was cleared
if not radius_address and st.session_state.radius_center is not None:
    st.session_state.radius_center = None

radius_center: tuple[float, float] | None = st.session_state.radius_center

# ---------------------------------------------------------------------------
# Filter and build base rows
# ---------------------------------------------------------------------------

def _fmt_last_sale(sale: dict | None) -> str:
    """Format a last-sale dict as 'e.g. $1.2M · 2022' for map tooltips."""
    if not sale:
        return "—"
    price = sale.get("sale_price")
    date_str = sale.get("sale_date")
    parts = []
    if price:
        if price >= 1_000_000:
            parts.append(f"${price / 1_000_000:.1f}M")
        elif price >= 1_000:
            parts.append(f"${price / 1_000:.0f}K")
        else:
            parts.append(f"${price:,.0f}")
    if date_str:
        parts.append(date_str[:4])
    return " · ".join(parts) if parts else "—"


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
    if radius_center is not None:
        dist = haversine_miles(radius_center[0], radius_center[1], float(lat), float(lng))
        if dist > radius_miles:
            continue
    if lat_range and not (lat_range[0] <= float(lat) <= lat_range[1]):
        continue
    if lng_range and not (lng_range[0] <= float(lng) <= lng_range[1]):
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
        "last_sale_fmt": _fmt_last_sale(last_sales.get(p["id"])),
    })

if not rows:
    if not properties:
        st.info("No properties found. Run `python data/ingest_nyc_open_data.py` to populate.")
    elif all(p.get("lat") is None for p in properties):
        st.warning("Properties loaded but none have coordinates. Re-run ingestion without `--no-geocode`.")
    elif radius_center is not None:
        st.warning(f"No properties within {radius_miles} mi of that address. Try a larger radius.")
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
# Radius circle layer (shared between cluster and normal modes)
# ---------------------------------------------------------------------------
def build_radius_layer(lat: float, lng: float, miles: float) -> pdk.Layer:
    ring = circle_polygon(lat, lng, miles)
    df_circle = pd.DataFrame([{"polygon": ring, "center": [lng, lat]}])
    return pdk.Layer(
        "PolygonLayer",
        data=df_circle,
        get_polygon="polygon",
        get_fill_color=[59, 130, 246, 40],
        get_line_color=[59, 130, 246, 200],
        get_line_width=3,
        line_width_min_pixels=2,
        pickable=False,
        stroked=True,
        filled=True,
    )


# ---------------------------------------------------------------------------
# Violation heatmap data
# ---------------------------------------------------------------------------

def build_heatmap_data(properties: list[dict], open_only: bool) -> list[dict]:
    """Return [{lat, lng, weight}] for properties that have violations and coordinates."""
    vcounts = load_violation_counts(open_only=open_only)
    heat_rows = []
    for p in properties:
        lat = p.get("lat")
        lng = p.get("lng")
        weight = vcounts.get(p["id"], 0)
        if lat and lng and weight > 0:
            heat_rows.append({"lat": float(lat), "lng": float(lng), "weight": weight})
    return heat_rows


# ---------------------------------------------------------------------------
# Compute initial view — center on radius if active, otherwise NYC default
# ---------------------------------------------------------------------------
if radius_center:
    view_lat, view_lng = radius_center
    view_zoom = max(10, 14 - int(radius_miles * 1.2))
else:
    view_lat, view_lng = 40.7128, -74.0060
    view_zoom = 10

initial_view = pdk.ViewState(latitude=view_lat, longitude=view_lng, zoom=view_zoom)

# ---------------------------------------------------------------------------
# Build layers + render map
# ---------------------------------------------------------------------------
selected_prop = None

# Build heatmap layer if enabled (shared across both pin and cluster modes)
heatmap_layer = None
if show_heatmap:
    open_only = (heatmap_mode == "Open only")
    heat_rows = build_heatmap_data(properties, open_only=open_only)
    if heat_rows:
        heatmap_layer = pdk.Layer(
            "HeatmapLayer",
            data=pd.DataFrame(heat_rows),
            get_position=["lng", "lat"],
            get_weight="weight",
            aggregation="SUM",
            radius_pixels=80,
            intensity=1,
            threshold=0.05,
            color_range=[
                [0, 0, 255, 0],
                [0, 255, 255, 80],
                [0, 255, 0, 120],
                [255, 255, 0, 160],
                [255, 128, 0, 200],
                [255, 0, 0, 230],
            ],
        )
    else:
        st.sidebar.info("No violations data yet. Run `python data/ingest_violations.py` to populate.")

if cluster_mode:
    cluster_rows, label_rows = build_clusters(rows, cluster_precision)
    df_clusters = pd.DataFrame(cluster_rows)
    df_labels = pd.DataFrame(label_rows)

    layers = []
    if show_subway:
        layers.extend(_build_subway_layers())

    layers += [
        pdk.Layer("ScatterplotLayer", data=df_clusters, get_position=["lng", "lat"],
                  get_fill_color="color", get_radius="count", radius_scale=120,
                  radius_min_pixels=10, radius_max_pixels=60, pickable=True),
        pdk.Layer("TextLayer", data=df_labels, get_position=["lng", "lat"],
                  get_text="label", get_size=14, get_color=[255, 255, 255, 230],
                  get_alignment_baseline="'center'", get_text_anchor="'middle'"),
    ]
    if heatmap_layer:
        layers.insert(0, heatmap_layer)  # heatmap at bottom
    if radius_center:
        layers.insert(1 if heatmap_layer else 0, build_radius_layer(radius_center[0], radius_center[1], radius_miles))

    tooltip = {
        "html": "<b>{count} properties</b><br/>{summary}",
        "style": {"backgroundColor": "#1e293b", "color": "white",
                  "fontSize": "13px", "padding": "8px", "borderRadius": "4px"},
    }
    st.pydeck_chart(pdk.Deck(layers=layers, initial_view_state=initial_view,
                              tooltip=tooltip, map_style=map_style))

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
        "html": "<b>{address}</b><br/>{deal_label} · {borough}<br/>Price: {price_fmt}<br/>Last sale: {last_sale_fmt}<br/>Pipeline: {pipeline_fmt}",
        "style": {"backgroundColor": "#1e293b", "color": "white",
                  "fontSize": "13px", "padding": "8px", "borderRadius": "4px"},
    }

    layers = []
    if show_subway:
        layers.extend(_build_subway_layers())
    if heatmap_layer:
        layers.insert(0, heatmap_layer)  # heatmap at bottom
    if radius_center:
        layers.insert(1 if heatmap_layer else 0, build_radius_layer(radius_center[0], radius_center[1], radius_miles))
    layers.append(scatter_layer)

    deck = pdk.Deck(
        layers=layers,
        initial_view_state=initial_view,
        tooltip=tooltip,
        map_style=map_style,
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
# Bounds-filtered property list
# ---------------------------------------------------------------------------
if filter_by_bounds and rows:
    st.divider()
    st.subheader(f"Properties in view ({len(rows)})")
    list_cols = st.columns(3)
    for i, row in enumerate(rows):
        prop = prop_by_index[i]
        pid = prop["id"]
        price = row.get("price")
        vcount = violation_counts.get(pid, 0)
        pipeline_status = tracked.get(pid)
        with list_cols[i % 3]:
            with st.container(border=True):
                icon = DEAL_ICONS.get(row["deal_type"], "⚪")
                st.markdown(f"**{row['address']}**")
                st.caption(f"{icon} {row['deal_label']} · {row['borough']}")
                c1, c2 = st.columns(2)
                c1.metric("Price", f"${price:,.0f}" if price else "—")
                c2.metric("Violations", vcount)
                if pipeline_status:
                    st.caption(PIPELINE_LABELS.get(pipeline_status, pipeline_status))
                if st.button("View Details →", key=f"bounds_detail_{pid}", use_container_width=True):
                    st.query_params["property_id"] = pid
                    st.switch_page("pages/5_Property_Detail.py")

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
    if show_heatmap and heatmap_layer:
        mode_label = "open" if (heatmap_mode == "Open only") else "all"
        st.caption(f"🌡 Heatmap = {mode_label} violation density (blue → red = low → high)")

with col_stats:
    if radius_center:
        st.caption(
            f"**{mapped}** properties within **{radius_miles} mi** of "
            f"_{st.session_state.radius_address_last}_"
        )
    elif cluster_mode:
        cluster_count = len(build_clusters(rows, cluster_precision)[0])
        st.caption(f"**{mapped}** properties in **{cluster_count}** clusters")
    else:
        bounds_note = " · filtered to map bounds" if filter_by_bounds else " · click a pin for details"
        st.caption(f"**{mapped}** pins shown{bounds_note}")
    if no_coords:
        st.caption(f"*{no_coords} properties skipped (no coordinates)*")
