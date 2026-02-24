"""Deal Feed — filterable list of property cards (TES-9/TES-10/TES-29/TES-31)."""

from __future__ import annotations

import csv
import io
import statistics
from collections import Counter
from datetime import date, datetime, timezone

import streamlit as st
from db import (
    add_to_pipeline,
    count_new_matches,
    load_deals,
    load_properties,
    load_saved_searches,
    load_violation_counts,
    mark_search_checked,
    save_search,
)

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

try:
    saved_searches = load_saved_searches()
except Exception:
    saved_searches = []

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

    st.divider()
    with st.expander("💾 Save this search"):
        _search_name = st.text_input(
            "Name", placeholder="e.g. BK Foreclosures under $1M", key="_save_search_name"
        )
        if st.button("Save Search", use_container_width=True, disabled=not _search_name.strip()):
            _filters = {
                "boroughs": borough_sel,
                "deal_types": [DEAL_TYPE_OPTIONS[k] for k in deal_type_sel],
                "prop_types": [PROP_TYPE_OPTIONS[k] for k in prop_type_sel],
                "price_min": int(price_range[0]) if price_range else None,
                "price_max": int(price_range[1]) if price_range else None,
                "min_beds": int(min_beds[0]) if min_beds != "Any" else None,
                "hide_tracked": hide_tracked,
            }
            try:
                save_search(_search_name.strip(), _filters)
                st.success("Saved! You'll be alerted when new matches appear.")
            except Exception as _exc:
                st.error(f"Failed to save: {_exc}")

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
DEAL_COLORS_HEX = {
    "foreclosure": "#dc2626",
    "tax_lien": "#ea580c",
    "listing": "#2563eb",
    "off_market": "#7c3aed",
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


# ---------------------------------------------------------------------------
# Sidebar: Neighborhood stats panel (shown when borough filter is active)
# ---------------------------------------------------------------------------
if borough_sel:
    area_label = ", ".join(borough_sel) if len(borough_sel) <= 2 else f"{len(borough_sel)} boroughs"

    with st.sidebar:
        st.divider()
        st.header("Area Stats")
        st.caption(area_label)

        if not filtered:
            st.caption("*No properties match current filters.*")
        else:
            # Deal count
            st.metric("Properties", len(filtered))

            # Median price
            prices_f = [p["price"] for p in filtered if p.get("price")]
            if prices_f:
                st.metric("Median Price", f"${statistics.median(prices_f):,.0f}")
            else:
                st.metric("Median Price", "—")

            # Avg days listed
            days_f = [d for d in (_days_ago(p.get("listed_at")) for p in filtered) if d is not None]
            if days_f:
                avg_days = sum(days_f) / len(days_f)
                st.metric("Avg Days Listed", f"{avg_days:.0f}d")

            # Deal type breakdown (only when enough data)
            if len(filtered) >= 3:
                st.caption("**Deal Type Breakdown**")
                type_counts = Counter(p.get("deal_type", "unknown") for p in filtered)
                total_f = len(filtered)
                for dt, count in type_counts.most_common():
                    lbl = DEAL_LABELS.get(dt, dt)
                    pct = count / total_f * 100
                    color = DEAL_COLORS_HEX.get(dt, "#888")
                    st.markdown(
                        f"<div style='margin-bottom:6px'>"
                        f"<span style='font-size:12px'>{lbl}</span>"
                        f"<div style='background:#1e293b;border-radius:4px;height:8px;margin-top:2px'>"
                        f"<div style='background:{color};width:{pct:.0f}%;height:8px;border-radius:4px'></div>"
                        f"</div>"
                        f"<span style='font-size:11px;color:#94a3b8'>{count} ({pct:.0f}%)</span>"
                        f"</div>",
                        unsafe_allow_html=True,
                    )
            else:
                st.caption("*Select more properties to see deal type breakdown.*")


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
# CSV export helper
# ---------------------------------------------------------------------------
_CSV_COLUMNS = ["address", "borough", "zip_code", "deal_type", "price",
                "listed_at", "pipeline_status", "source_url"]


def _build_csv(props: list[dict]) -> str:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=_CSV_COLUMNS, extrasaction="ignore")
    writer.writeheader()
    for p in props:
        writer.writerow({
            "address": p.get("address", ""),
            "borough": p.get("borough", ""),
            "zip_code": p.get("zip_code", ""),
            "deal_type": p.get("deal_type", ""),
            "price": p.get("price", ""),
            "listed_at": p.get("listed_at", ""),
            "pipeline_status": tracked.get(p["id"], ""),
            "source_url": p.get("source_url", ""),
        })
    return buf.getvalue()


# ---------------------------------------------------------------------------
# New match alerts banner
# ---------------------------------------------------------------------------
if saved_searches:
    _alerts = [
        (s["name"], count_new_matches(s, all_properties), s["id"])
        for s in saved_searches
    ]
    _alerts = [(name, n, sid) for name, n, sid in _alerts if n > 0]
    if _alerts:
        _total_new = sum(n for _, n, _ in _alerts)
        _names = ", ".join(f'"{name}"' for name, _, _ in _alerts[:3])
        if len(_alerts) > 3:
            _names += f" and {len(_alerts) - 3} more"
        _col_msg, _col_link = st.columns([5, 1])
        _col_msg.info(
            f"🔔 **{_total_new} new {'match' if _total_new == 1 else 'matches'}** "
            f"for your saved searches: {_names}"
        )
        with _col_link:
            st.page_link("pages/6_Alerts.py", label="View Alerts →")

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

    header_col, export_col = st.columns([3, 1])
    header_col.caption(label)
    export_col.download_button(
        label="⬇ Export CSV",
        data=_build_csv(filtered),
        file_name=f"nyc-deals-{date.today()}.csv",
        mime="text/csv",
        use_container_width=True,
    )

    cols = st.columns(3)
    for i, prop in enumerate(filtered):
        with cols[i % 3]:
            _render_card(prop, viol_count=violation_counts.get(prop["id"], 0))
