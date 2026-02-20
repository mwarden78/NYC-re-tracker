"""Deal Feed — filterable list of property cards (TES-9)."""

from __future__ import annotations

from datetime import datetime, timezone

import streamlit as st
from db import add_to_pipeline, load_deals, load_properties

st.set_page_config(page_title="Deal Feed | NYC RE Tracker", page_icon="🏠", layout="wide")

st.title("Deal Feed")
st.caption("Browse foreclosure and tax lien properties")

# ---------------------------------------------------------------------------
# Sidebar filters
# ---------------------------------------------------------------------------
with st.sidebar:
    st.header("Filters")

    BOROUGHS = ["All", "Brooklyn", "Queens", "Manhattan", "Bronx", "Staten Island"]
    borough_sel = st.selectbox("Borough", BOROUGHS)

    DEAL_TYPES = {
        "All": None,
        "Foreclosure": "foreclosure",
        "Tax Lien": "tax_lien",
        "Listing": "listing",
        "Off Market": "off_market",
    }
    deal_type_sel = st.selectbox("Deal Type", list(DEAL_TYPES.keys()))

# ---------------------------------------------------------------------------
# Load data
# ---------------------------------------------------------------------------
borough_filter = None if borough_sel == "All" else borough_sel
deal_type_filter = DEAL_TYPES[deal_type_sel]

try:
    properties = load_properties(borough=borough_filter, deal_type=deal_type_filter)
    deals = load_deals()
except Exception as e:
    st.error(f"Could not load data from Supabase. Check your `.env` file. ({e})")
    st.stop()

# Map property_id → pipeline status for quick lookup
tracked: dict[str, str] = {d["property_id"]: d["status"] for d in deals}

# ---------------------------------------------------------------------------
# Display helpers
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


def _days_ago(dt_str: str | None) -> int | None:
    if not dt_str:
        return None
    try:
        dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
        return (datetime.now(timezone.utc) - dt).days
    except Exception:
        return None


def _render_card(prop: dict) -> None:
    prop_id = prop["id"]
    deal_type = prop.get("deal_type", "")
    icon = DEAL_ICONS.get(deal_type, "⚪")
    label = DEAL_LABELS.get(deal_type, deal_type.replace("_", " ").title())
    borough = prop.get("borough", "")
    address = prop.get("address", "Unknown address")
    price = prop.get("price")
    sqft = prop.get("sqft")
    beds = prop.get("bedrooms")
    baths = prop.get("bathrooms")
    days = _days_ago(prop.get("listed_at"))
    pipeline_status = tracked.get(prop_id)

    with st.container(border=True):
        left, right = st.columns([2, 1])
        left.markdown(f"**{icon} {label}**")
        right.markdown(
            f"<p style='text-align:right;margin:0'>{borough}</p>",
            unsafe_allow_html=True,
        )

        st.markdown(f"**{address}**")

        if price:
            st.markdown(f"### ${price:,.0f}")
        else:
            st.markdown("*Price unknown*")

        details = []
        if sqft:
            details.append(f"{sqft:,} sqft")
        if beds is not None:
            details.append(f"{int(beds)} bed")
        if baths is not None:
            details.append(f"{baths:g} bath")
        if details:
            st.caption(" · ".join(details))

        if days is not None:
            st.caption(f"Listed {days}d ago")

        st.divider()

        if pipeline_status:
            st.caption(PIPELINE_LABELS.get(pipeline_status, pipeline_status))
        else:
            if st.button("+ Watch", key=f"watch_{prop_id}", use_container_width=True):
                try:
                    add_to_pipeline(prop_id, "watching")
                    st.rerun()
                except Exception as exc:
                    st.error(f"Failed to add to pipeline: {exc}")


# ---------------------------------------------------------------------------
# Property grid
# ---------------------------------------------------------------------------
if not properties:
    st.info(
        "No properties found. "
        "Run `python data/ingest_nyc_open_data.py` to populate, or adjust filters."
    )
else:
    st.caption(f"{len(properties)} propert{'y' if len(properties) == 1 else 'ies'}")

    cols = st.columns(3)
    for i, prop in enumerate(properties):
        with cols[i % 3]:
            _render_card(prop)
