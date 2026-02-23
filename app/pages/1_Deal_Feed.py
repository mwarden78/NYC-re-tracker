"""Deal Feed — filterable list of property cards (TES-9/TES-10)."""

from __future__ import annotations

from datetime import datetime, timezone

import streamlit as st
from db import add_to_pipeline, load_deals, load_properties, load_violation_counts

st.set_page_config(page_title="Deal Feed | NYC RE Tracker", page_icon="🏠", layout="wide")

st.title("Deal Feed")
st.caption("Browse foreclosure and tax lien properties")

# ---------------------------------------------------------------------------
# Load all data up front so filter ranges reflect the full dataset
# ---------------------------------------------------------------------------
try:
    all_properties = load_properties()
    deals = load_deals()
    violation_counts = load_violation_counts()
except Exception as e:
    st.error(f"Could not load data from Supabase. Check your `.env` file. ({e})")
    st.stop()

tracked: dict[str, str] = {d["property_id"]: d["status"] for d in deals}

# ---------------------------------------------------------------------------
# Sidebar filter panel
# ---------------------------------------------------------------------------
with st.sidebar:
    st.header("Filters")

    # Borough — multiselect
    BOROUGHS = ["Brooklyn", "Queens", "Manhattan", "Bronx", "Staten Island"]
    borough_sel = st.multiselect("Borough", BOROUGHS, placeholder="All boroughs")

    # Deal type — multiselect
    DEAL_TYPE_OPTIONS = {
        "Foreclosure": "foreclosure",
        "Tax Lien": "tax_lien",
        "Listing": "listing",
        "Off Market": "off_market",
    }
    deal_type_sel = st.multiselect(
        "Deal Type", list(DEAL_TYPE_OPTIONS.keys()), placeholder="All types"
    )

    # Property type — multiselect
    PROP_TYPE_OPTIONS = {
        "1-4 Family": "1-4 family",
        "Multifamily": "multifamily",
        "Condo": "condo",
        "Co-op": "co-op",
        "Townhouse": "townhouse",
        "Land": "land",
    }
    prop_type_sel = st.multiselect(
        "Property Type", list(PROP_TYPE_OPTIONS.keys()), placeholder="All types"
    )

    st.divider()

    # Price range slider — derived from actual data
    prices = [p["price"] for p in all_properties if p.get("price")]
    if prices:
        price_min_data = int(min(prices))
        price_max_data = int(max(prices))
        price_range = st.slider(
            "Price Range",
            min_value=price_min_data,
            max_value=price_max_data,
            value=(price_min_data, price_max_data),
            step=10_000,
            format="$%d",
        )
    else:
        price_range = None

    # Min bedrooms
    min_beds = st.selectbox("Min Bedrooms", ["Any", "1+", "2+", "3+", "4+", "5+"])

    st.divider()

    # Sort
    SORT_OPTIONS = {
        "Newest first": ("listed_at", True),
        "Price: low to high": ("price", False),
        "Price: high to low": ("price", True),
    }
    sort_sel = st.selectbox("Sort By", list(SORT_OPTIONS.keys()))

    # Hide tracked
    hide_tracked = st.checkbox("Hide tracked properties", value=False)

    # Reset button
    if st.button("Reset Filters", use_container_width=True):
        st.rerun()

# ---------------------------------------------------------------------------
# Apply filters client-side
# ---------------------------------------------------------------------------
deal_type_values = {DEAL_TYPE_OPTIONS[k] for k in deal_type_sel}
prop_type_values = {PROP_TYPE_OPTIONS[k] for k in prop_type_sel}
min_beds_num = int(min_beds[0]) if min_beds != "Any" else 0

filtered = []
for p in all_properties:
    if borough_sel and p.get("borough") not in borough_sel:
        continue
    if deal_type_values and p.get("deal_type") not in deal_type_values:
        continue
    if prop_type_values and p.get("property_type") not in prop_type_values:
        continue
    if price_range:
        price = p.get("price")
        if price is not None and not (price_range[0] <= price <= price_range[1]):
            continue
    if min_beds_num > 0:
        beds = p.get("bedrooms")
        if beds is None or int(beds) < min_beds_num:
            continue
    if hide_tracked and p["id"] in tracked:
        continue
    filtered.append(p)

# Sort
sort_key, sort_desc = SORT_OPTIONS[sort_sel]


def _sort_val(p: dict):
    v = p.get(sort_key)
    if v is None:
        return (1, 0)  # push nulls to end
    return (0, v)


filtered.sort(key=_sort_val, reverse=sort_desc)

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


def _render_card(prop: dict, viol_count: int = 0) -> None:
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
    prop_type = prop.get("property_type", "")
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
        if prop_type:
            st.caption(prop_type.title())

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

        if viol_count > 0:
            st.caption(f"⚠️ {viol_count} violation{'s' if viol_count != 1 else ''}")

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

        if st.button("View Details →", key=f"detail_{prop_id}", use_container_width=True):
            st.query_params["property_id"] = prop_id
            st.switch_page("pages/5_Property_Detail.py")


# ---------------------------------------------------------------------------
# Property grid
# ---------------------------------------------------------------------------
total = len(all_properties)
shown = len(filtered)

if total == 0:
    st.info(
        "No properties found. "
        "Run `python data/ingest_nyc_open_data.py` to populate."
    )
elif shown == 0:
    st.warning("No properties match your current filters.")
    st.caption(f"0 of {total} properties shown")
else:
    label = f"{shown} propert{'y' if shown == 1 else 'ies'}"
    if shown < total:
        label += f" (filtered from {total})"
    st.caption(label)

    cols = st.columns(3)
    for i, prop in enumerate(filtered):
        with cols[i % 3]:
            _render_card(prop, viol_count=violation_counts.get(prop["id"], 0))
