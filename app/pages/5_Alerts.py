"""Alerts — saved searches and new match notifications (TES-31)."""

from __future__ import annotations

from datetime import datetime

import streamlit as st
from db import (
    count_new_matches,
    delete_saved_search,
    load_properties,
    load_saved_searches,
    mark_search_checked,
)
from sidebar import render_more_section

st.set_page_config(page_title="Alerts | NYC RE Tracker", page_icon="🔔", layout="wide")
render_more_section()

st.title("Alerts")
st.caption("Saved searches — get notified when new properties match your filters")

# ---------------------------------------------------------------------------
# Load data
# ---------------------------------------------------------------------------
try:
    all_properties = load_properties()
    saved_searches = load_saved_searches()
except Exception as e:
    st.error(f"Could not load data from Supabase. Check your `.env` file. ({e})")
    st.stop()

if not saved_searches:
    st.info(
        "No saved searches yet. Go to the **Deal Feed**, set your filters, "
        "and click **Save this search** in the sidebar."
    )
    st.page_link("pages/1_Deal_Feed.py", label="Go to Deal Feed →")
    st.stop()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
DEAL_LABELS = {
    "foreclosure": "Foreclosure",
    "pre_foreclosure": "Pre-Foreclosure",
    "tax_lien": "Tax Lien",
    "listing": "Listing",
    "off_market": "Off Market",
}


def _filter_summary(filters: dict) -> str:
    parts = []
    if filters.get("boroughs"):
        parts.append(", ".join(filters["boroughs"]))
    if filters.get("deal_types"):
        parts.append(" · ".join(DEAL_LABELS.get(dt, dt) for dt in filters["deal_types"]))
    if filters.get("prop_types"):
        parts.append(", ".join(filters["prop_types"]))
    price_min = filters.get("price_min")
    price_max = filters.get("price_max")
    if price_min and price_max:
        parts.append(f"${price_min:,}–${price_max:,}")
    elif price_min:
        parts.append(f"\u2265 ${price_min:,}")
    elif price_max:
        parts.append(f"\u2264 ${price_max:,}")
    if filters.get("min_beds"):
        parts.append(f"{filters['min_beds']}+ beds")
    return " · ".join(parts) if parts else "All properties"


# ---------------------------------------------------------------------------
# Summary row + bulk action
# ---------------------------------------------------------------------------
total_new = sum(count_new_matches(s, all_properties) for s in saved_searches)

col_summary, col_bulk = st.columns([3, 1])
col_summary.caption(
    f"**{len(saved_searches)}** saved search{'es' if len(saved_searches) != 1 else ''}"
    + (f" · **{total_new} new match{'es' if total_new != 1 else ''}**" if total_new else "")
)
if total_new > 0 and col_bulk.button("Mark all as seen", use_container_width=True):
    for s in saved_searches:
        mark_search_checked(s["id"])
    st.rerun()

st.divider()

# ---------------------------------------------------------------------------
# Saved search cards
# ---------------------------------------------------------------------------
for search in saved_searches:
    new_count = count_new_matches(search, all_properties)
    filters = search.get("filters", {})

    with st.container(border=True):
        col_info, col_badge, col_actions = st.columns([4, 1, 1])

        with col_info:
            st.markdown(f"### {search['name']}")
            st.caption(_filter_summary(filters))
            created = search.get("created_at", "")
            if created:
                try:
                    dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
                    st.caption(f"Saved {dt.strftime('%b')} {dt.day}, {dt.year}")
                except Exception:
                    pass

        with col_badge:
            if new_count > 0:
                st.markdown(
                    f"<div style='background:#dc2626;color:white;border-radius:20px;"
                    f"padding:6px 14px;text-align:center;font-weight:bold;font-size:22px;"
                    f"margin-top:8px'>{new_count}</div>"
                    f"<p style='text-align:center;font-size:12px;color:#94a3b8;margin:4px 0 0'>"
                    f"new {'match' if new_count == 1 else 'matches'}</p>",
                    unsafe_allow_html=True,
                )
            else:
                st.markdown(
                    "<p style='color:#94a3b8;margin-top:18px;text-align:center'>"
                    "No new matches</p>",
                    unsafe_allow_html=True,
                )

        with col_actions:
            st.write("")  # vertical spacer
            if new_count > 0:
                if st.button("Mark seen", key=f"seen_{search['id']}", use_container_width=True):
                    mark_search_checked(search["id"])
                    st.rerun()
            if st.button(
                "Delete", key=f"del_{search['id']}", use_container_width=True, type="secondary"
            ):
                delete_saved_search(search["id"])
                st.rerun()
