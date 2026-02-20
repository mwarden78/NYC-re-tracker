"""Pipeline — track deals through the investment workflow (TES-12)."""

from __future__ import annotations

import streamlit as st
from db import load_deals, update_deal_status

st.set_page_config(page_title="Pipeline | NYC RE Tracker", page_icon="📋", layout="wide")

st.title("Pipeline")
st.caption("Track deals: Watching → Analyzing → Offer Made → Dead")

STAGES = ["watching", "analyzing", "offer_made", "dead"]

STAGE_LABELS = {
    "watching": "Watching",
    "analyzing": "Analyzing",
    "offer_made": "Offer Made",
    "dead": "Dead",
}

STAGE_ICONS = {
    "watching": "👁️",
    "analyzing": "🔍",
    "offer_made": "📋",
    "dead": "💀",
}


def _format_price(price) -> str:
    if not price:
        return "N/A"
    if price >= 1_000_000:
        return f"${price / 1_000_000:.1f}M"
    return f"${price:,.0f}"


def _render_deal_card(deal: dict) -> None:
    prop = deal.get("properties") or {}

    st.markdown(f"**{prop.get('address', 'Unknown address')}**")
    borough = prop.get("borough", "")
    deal_type = (prop.get("deal_type") or "").replace("_", " ").title()
    st.caption(f"{borough} · {deal_type}")

    col1, col2 = st.columns(2)
    with col1:
        st.metric("Price", _format_price(prop.get("price")))
    with col2:
        sqft = prop.get("sqft")
        st.metric("Sqft", f"{sqft:,}" if sqft else "N/A")

    if deal.get("notes"):
        st.caption(f"📝 {deal['notes']}")

    current_status = deal.get("status", "watching")
    new_status = st.selectbox(
        "Stage",
        options=STAGES,
        index=STAGES.index(current_status) if current_status in STAGES else 0,
        format_func=lambda s: f"{STAGE_ICONS[s]} {STAGE_LABELS[s]}",
        key=f"status_{deal['id']}",
        label_visibility="collapsed",
    )
    if new_status != current_status:
        update_deal_status(deal["id"], new_status)
        st.rerun()


try:
    deals = load_deals()
except Exception as e:
    st.error(f"Could not load deals from Supabase. Check your `.env` file. ({e})")
    st.stop()

if not deals:
    st.info("No deals in your pipeline yet. Browse the Deal Feed and click **+ Watch** to start tracking.")
    st.stop()

# Group deals by stage
by_stage: dict[str, list] = {stage: [] for stage in STAGES}
for deal in deals:
    status = deal.get("status", "watching")
    by_stage.setdefault(status, []).append(deal)

# Summary metrics
summary_cols = st.columns(4)
for i, stage in enumerate(STAGES):
    count = len(by_stage[stage])
    with summary_cols[i]:
        st.metric(
            label=f"{STAGE_ICONS[stage]} {STAGE_LABELS[stage]}",
            value=count,
        )

st.divider()

# Pipeline columns
pipeline_cols = st.columns(4)
for i, stage in enumerate(STAGES):
    with pipeline_cols[i]:
        stage_deals = by_stage[stage]
        st.subheader(f"{STAGE_ICONS[stage]} {STAGE_LABELS[stage]}")
        st.caption(f"{len(stage_deals)} deal{'s' if len(stage_deals) != 1 else ''}")

        if not stage_deals:
            st.markdown(
                "<div style='color:#888;font-size:0.85rem;padding:8px 0'>No deals here</div>",
                unsafe_allow_html=True,
            )
        else:
            for deal in stage_deals:
                with st.container(border=True):
                    _render_deal_card(deal)
