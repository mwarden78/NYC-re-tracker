"""Shared sidebar helpers — rendered on every main page (TES-98)."""

from __future__ import annotations

import streamlit as st


def render_more_section() -> None:
    """Add quick search and collapsed 'More' expander to the sidebar."""
    with st.sidebar:
        _render_quick_search()
        st.divider()
        with st.expander("⋯ More"):
            if st.button("➕ Add Deal", use_container_width=True):
                st.switch_page("pages/_Add_Deal.py")


def _render_quick_search() -> None:
    """Render a quick-search input that jumps to Property Detail on match."""
    query = st.text_input(
        "Search properties",
        placeholder="Address, borough, or BBL...",
        key="_sidebar_search",
        label_visibility="collapsed",
    )
    if not query or len(query) < 2:
        return

    from db import load_properties  # deferred to avoid circular import on first load

    properties = load_properties()
    q = query.strip().lower()

    matches = []
    for p in properties:
        address = (p.get("address") or "").lower()
        borough = (p.get("borough") or "").lower()
        bbl = (p.get("bbl") or "").lower()
        zip_code = (p.get("zip_code") or "").lower()
        if q in address or q in borough or q in bbl or q in zip_code:
            matches.append(p)
        if len(matches) >= 5:
            break

    if not matches:
        st.caption("No matches found.")
        return

    for p in matches:
        deal_type = (p.get("deal_type") or "").replace("_", " ").title()
        label = f"{p.get('address', '?')} · {p.get('borough', '')}"
        if deal_type:
            label += f" · {deal_type}"
        if st.button(label, key=f"search_{p['id']}", use_container_width=True):
            st.query_params["property_id"] = p["id"]
            st.switch_page("pages/_Property_Detail.py")
