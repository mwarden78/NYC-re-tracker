"""Pipeline — track deals through the investment workflow (TES-12, TES-92, TES-93)."""

from __future__ import annotations

import statistics
from datetime import datetime, timezone

import streamlit as st
from db import load_deals, load_violation_counts, update_deal_status
from sidebar import render_more_section

st.set_page_config(page_title="Pipeline | NYC RE Tracker", page_icon="📋", layout="wide")
render_more_section()

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


def _deal_age(created_at: str | None) -> str | None:
    """Return a human-readable deal age like '3d', '2w', '1mo' from created_at."""
    if not created_at:
        return None
    try:
        dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        days = (datetime.now(timezone.utc) - dt).days
        if days < 1:
            return "today"
        if days < 7:
            return f"{days}d"
        if days < 30:
            return f"{days // 7}w"
        return f"{days // 30}mo"
    except Exception:
        return None


def _render_deal_card(deal: dict) -> None:
    prop = deal.get("properties") or {}

    st.markdown(f"**{prop.get('address', 'Unknown address')}**")
    borough = prop.get("borough", "")
    deal_type = (prop.get("deal_type") or "").replace("_", " ").title()
    age = _deal_age(deal.get("created_at"))
    age_suffix = f" · {age}" if age else ""
    st.caption(f"{borough} · {deal_type}{age_suffix}")

    col1, col2 = st.columns(2)
    with col1:
        st.metric("Price", _format_price(prop.get("price")))
    with col2:
        sqft = prop.get("sqft")
        st.metric("Sqft", f"{sqft:,}" if sqft else "N/A")

    if deal.get("notes"):
        st.caption(f"📝 {deal['notes']}")

    viol_count = violation_counts.get(prop.get("id", ""), 0)
    if viol_count > 0:
        st.caption(f"⚠️ {viol_count} violation{'s' if viol_count != 1 else ''}")

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

    prop_id = prop.get("id")
    if prop_id and st.button("View Details →", key=f"detail_{deal['id']}", use_container_width=True):
        st.query_params["property_id"] = prop_id
        st.switch_page("pages/_Property_Detail.py")


try:
    deals = load_deals()
    violation_counts = load_violation_counts()
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

# ---------------------------------------------------------------------------
# Value summary helpers
# ---------------------------------------------------------------------------

def _stage_prices(stage_deals: list[dict]) -> list[float]:
    """Extract non-None prices from deals in a stage."""
    return [
        p for d in stage_deals
        if (p := (d.get("properties") or {}).get("price")) is not None
    ]


def _fmt_value(value: float) -> str:
    if value >= 1_000_000:
        return f"${value / 1_000_000:.1f}M"
    if value >= 1_000:
        return f"${value / 1_000:.0f}K"
    return f"${value:,.0f}"


# ---------------------------------------------------------------------------
# Summary metrics — count + value per stage
# ---------------------------------------------------------------------------
summary_cols = st.columns(4)
for i, stage in enumerate(STAGES):
    stage_deals = by_stage[stage]
    count = len(stage_deals)
    prices = _stage_prices(stage_deals)
    total_val = sum(prices)
    with summary_cols[i]:
        st.metric(
            label=f"{STAGE_ICONS[stage]} {STAGE_LABELS[stage]}",
            value=count,
        )
        if total_val:
            st.caption(f"Total: {_fmt_value(total_val)}")

# ---------------------------------------------------------------------------
# Portfolio value summary bar
# ---------------------------------------------------------------------------
active_stages = ["watching", "analyzing", "offer_made"]  # exclude dead
all_active_prices = []
for s in active_stages:
    all_active_prices.extend(_stage_prices(by_stage[s]))

if all_active_prices:
    total_portfolio = sum(all_active_prices)
    median_price = statistics.median(all_active_prices)
    priced_count = len(all_active_prices)
    active_count = sum(len(by_stage[s]) for s in active_stages)
    unpriced = active_count - priced_count

    st.divider()
    vcol1, vcol2, vcol3, vcol4 = st.columns(4)
    vcol1.metric("Active Portfolio Value", _fmt_value(total_portfolio))
    vcol2.metric("Median Deal Price", _fmt_value(median_price))
    vcol3.metric("Deals with Price", f"{priced_count}/{active_count}")
    if len(all_active_prices) >= 2:
        vcol4.metric("Price Range", f"{_fmt_value(min(all_active_prices))} – {_fmt_value(max(all_active_prices))}")

st.divider()

# Pipeline columns
pipeline_cols = st.columns(4)
for i, stage in enumerate(STAGES):
    with pipeline_cols[i]:
        stage_deals = by_stage[stage]
        prices = _stage_prices(stage_deals)
        st.subheader(f"{STAGE_ICONS[stage]} {STAGE_LABELS[stage]}")
        value_note = f" · {_fmt_value(sum(prices))}" if prices else ""
        st.caption(f"{len(stage_deals)} deal{'s' if len(stage_deals) != 1 else ''}{value_note}")

        if not stage_deals:
            st.markdown(
                "<div style='color:#888;font-size:0.85rem;padding:8px 0'>No deals here</div>",
                unsafe_allow_html=True,
            )
        else:
            for deal in stage_deals:
                with st.container(border=True):
                    _render_deal_card(deal)
