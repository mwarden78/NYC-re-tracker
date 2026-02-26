"""Shared sidebar helpers — rendered on every main page (TES-78, TES-98)."""

from __future__ import annotations

import streamlit as st

# ---------------------------------------------------------------------------
# Light mode CSS overrides — injected when light mode is toggled on
# ---------------------------------------------------------------------------
_LIGHT_CSS = """
<style>
    /* Main background and text */
    [data-testid="stAppViewContainer"],
    [data-testid="stHeader"],
    [data-testid="stSidebar"],
    [data-testid="stSidebar"] > div:first-child,
    .main .block-container {
        background-color: #ffffff;
        color: #1a1a2e;
    }
    /* Sidebar */
    [data-testid="stSidebar"] {
        background-color: #f8f9fa;
    }
    [data-testid="stSidebar"] * {
        color: #1a1a2e;
    }
    /* Text elements */
    .main h1, .main h2, .main h3, .main h4,
    .main p, .main span, .main label, .main div {
        color: #1a1a2e !important;
    }
    /* Metric values */
    [data-testid="stMetricValue"] {
        color: #1a1a2e !important;
    }
    /* Captions */
    .stCaption, small {
        color: #6b7280 !important;
    }
    /* Containers with border */
    [data-testid="stVerticalBlock"] > div[data-testid="stExpander"],
    div[data-testid="stVerticalBlockBorderWrapper"] {
        border-color: #e5e7eb !important;
    }
    /* Buttons */
    .stButton > button {
        color: #1a1a2e;
        border-color: #d1d5db;
    }
    /* Selectbox and inputs */
    .stSelectbox > div > div,
    .stTextInput > div > div > input,
    .stTextArea > div > div > textarea {
        background-color: #f9fafb;
        color: #1a1a2e;
    }
</style>
"""


def render_more_section() -> None:
    """Add quick search, theme toggle, and collapsed 'More' expander to the sidebar."""
    _apply_theme()
    with st.sidebar:
        _render_quick_search()
        st.divider()
        with st.expander("⋯ More"):
            if st.button("➕ Add Deal", use_container_width=True):
                st.switch_page("pages/_Add_Deal.py")
            st.divider()
            light_mode = st.toggle(
                "Light mode",
                value=st.session_state.get("light_mode", False),
                key="_light_toggle",
            )
            if light_mode != st.session_state.get("light_mode", False):
                st.session_state["light_mode"] = light_mode
                st.rerun()


def _apply_theme() -> None:
    """Inject light-mode CSS overrides if the toggle is on."""
    if st.session_state.get("light_mode", False):
        st.markdown(_LIGHT_CSS, unsafe_allow_html=True)


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
