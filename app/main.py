"""NYC RE Tracker - Home page.

Run with:
  .venv/bin/streamlit run app/main.py
"""

import streamlit as st
from db import load_summary

st.set_page_config(
    page_title="NYC RE Tracker",
    page_icon="🏙",
    layout="wide",
)

st.title("NYC RE Tracker")
st.caption("NYC residential real estate deal tracker — value-add & distressed properties")

st.divider()

# --- Summary stats ---
try:
    summary = load_summary()

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Total Properties", summary["total_properties"])
    col2.metric("Foreclosures", summary["by_deal_type"].get("foreclosure", 0))
    col3.metric("Tax Liens", summary["by_deal_type"].get("tax_lien", 0))
    col4.metric("Active Deals", summary["total_deals"])

    st.divider()

    left, right = st.columns(2)

    with left:
        st.subheader("By Borough")
        by_borough = summary["by_borough"]
        if by_borough:
            for borough, count in sorted(by_borough.items(), key=lambda x: -x[1]):
                st.progress(
                    count / max(by_borough.values()),
                    text=f"{borough}: {count}",
                )
        else:
            st.caption("No data yet — run the ingestion script to populate.")

    with right:
        st.subheader("Pipeline")
        pipeline = summary["pipeline"]
        STATUS_LABELS = {
            "watching": "Watching",
            "analyzing": "Analyzing",
            "offer_made": "Offer Made",
            "dead": "Dead",
        }
        if pipeline:
            for status, label in STATUS_LABELS.items():
                count = pipeline.get(status, 0)
                st.metric(label, count)
        else:
            st.caption("No deals tracked yet — add one from the Pipeline page.")

except Exception as e:
    st.warning(
        f"Could not load data from Supabase. Check your `.env` file. ({e})",
        icon="⚠",
    )

st.divider()
st.caption("Use the sidebar to navigate between views.")
