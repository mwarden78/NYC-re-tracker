"""Shared sidebar helpers — rendered on every main page."""

from __future__ import annotations

import streamlit as st


def render_more_section() -> None:
    """Add a collapsed 'More' expander at the bottom of the sidebar.

    Contains links to secondary pages (Add Deal) that are hidden from the
    main auto-generated navigation.
    """
    with st.sidebar:
        st.divider()
        with st.expander("⋯ More"):
            if st.button("➕ Add Deal", use_container_width=True):
                st.switch_page("pages/_Add_Deal.py")
