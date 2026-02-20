"""Add Deal — manually enter a property into the tracker.

Implemented in TES-13.
"""

import streamlit as st

st.set_page_config(page_title="Add Deal | NYC RE Tracker", page_icon="➕", layout="wide")

st.title("Add Deal")
st.caption("Manually add an off-market or unlisted property")

st.info("Coming in TES-13 — Manual deal entry form.", icon="🔲")
