"""Add Deal — manually enter a property into the tracker (TES-13)."""

from __future__ import annotations

import streamlit as st
from db import add_to_pipeline, load_properties

st.set_page_config(page_title="Add Deal | NYC RE Tracker", page_icon="➕", layout="wide")

st.title("Add Deal")
st.caption("Manually add an off-market or unlisted property")

# ---------------------------------------------------------------------------
# Form
# ---------------------------------------------------------------------------
with st.form("add_deal_form", clear_on_submit=True):
    st.subheader("Property Details")

    col1, col2 = st.columns(2)

    with col1:
        address = st.text_input("Address *", placeholder="123 Atlantic Ave")
        borough = st.selectbox(
            "Borough *",
            ["Brooklyn", "Queens", "Manhattan", "Bronx", "Staten Island"],
        )
        zip_code = st.text_input("Zip Code", placeholder="11201", max_chars=5)
        deal_type = st.selectbox(
            "Deal Type *",
            options=["off_market", "foreclosure", "pre_foreclosure", "tax_lien", "listing"],
            format_func=lambda x: x.replace("_", " ").title(),
        )

    with col2:
        property_type = st.selectbox(
            "Property Type",
            options=["", "1-4 family", "multifamily", "condo", "co-op", "townhouse", "land"],
            format_func=lambda x: x.replace("-", "‑").title() if x else "— Select —",
        )
        price = st.number_input("Asking Price ($)", min_value=0, step=10_000, value=0)
        sqft = st.number_input("Square Feet", min_value=0, step=100, value=0)
        bedrooms = st.number_input("Bedrooms", min_value=0, step=1, value=0)
        bathrooms = st.number_input("Bathrooms", min_value=0.0, step=0.5, value=0.0)

    st.subheader("Pipeline")
    initial_status = st.selectbox(
        "Initial Pipeline Stage",
        options=["watching", "analyzing", "offer_made"],
        format_func=lambda x: x.replace("_", " ").title(),
    )
    notes = st.text_area("Notes", placeholder="Key details, why this deal is interesting...")

    submitted = st.form_submit_button("Add to Pipeline", type="primary", use_container_width=True)

# ---------------------------------------------------------------------------
# Handle submission
# ---------------------------------------------------------------------------
if submitted:
    if not address.strip():
        st.error("Address is required.")
    else:
        try:
            import sys, os
            sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
            from utils.supabase_client import get_client  # noqa: E402

            client = get_client()

            # Insert property
            prop_data: dict = {
                "address": address.strip(),
                "borough": borough,
                "deal_type": deal_type,
                "source": "manual",
            }
            if zip_code.strip():
                prop_data["zip_code"] = zip_code.strip()
            if property_type:
                prop_data["property_type"] = property_type
            if price > 0:
                prop_data["price"] = price
            if sqft > 0:
                prop_data["sqft"] = sqft
            if bedrooms > 0:
                prop_data["bedrooms"] = bedrooms
            if bathrooms > 0:
                prop_data["bathrooms"] = bathrooms

            result = client.table("properties").insert(prop_data).execute()
            new_property_id = result.data[0]["id"]

            # Create deal record
            deal_data: dict = {
                "property_id": new_property_id,
                "status": initial_status,
            }
            if notes.strip():
                deal_data["notes"] = notes.strip()

            client.table("deals").insert(deal_data).execute()
            st.cache_data.clear()

            st.success(
                f"✅ **{address.strip()}** added to your pipeline as "
                f"*{initial_status.replace('_', ' ').title()}*."
            )

        except Exception as e:
            st.error(f"Failed to save property: {e}")

# ---------------------------------------------------------------------------
# Recent manual entries
# ---------------------------------------------------------------------------
st.divider()
st.subheader("Recent Manual Entries")

try:
    props = load_properties()
    manual = [p for p in props if p.get("source") == "manual"][:10]

    if not manual:
        st.caption("No manually added properties yet.")
    else:
        for p in manual:
            price_str = f"${p['price']:,.0f}" if p.get("price") else "Price unknown"
            deal_str = (p.get("deal_type") or "").replace("_", " ").title()
            st.caption(f"**{p['address']}** — {p['borough']} · {deal_str} · {price_str}")
except Exception:
    pass
