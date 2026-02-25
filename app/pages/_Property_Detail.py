"""Property Detail page — TES-19, TES-45.

Navigated to via query param: ?property_id=<UUID>
"""

from __future__ import annotations

import pandas as pd
import pydeck as pdk
import streamlit as st
from db import (
    add_to_pipeline,
    load_deal_by_property,
    load_lien_history_by_property,
    load_property_by_id,
    load_sale_history,
    load_violations_by_property,
    update_deal_notes,
    update_deal_status,
)

st.set_page_config(page_title="Property Detail | NYC RE Tracker", page_icon="🏠", layout="wide")

# ---------------------------------------------------------------------------
# Constants
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
PIPELINE_LABELS = {
    "watching": "👁 Watching",
    "analyzing": "🔍 Analyzing",
    "offer_made": "📝 Offer Made",
    "dead": "💀 Dead",
}
STAGES = ["watching", "analyzing", "offer_made", "dead"]

# ---------------------------------------------------------------------------
# Resolve property_id from query params
# ---------------------------------------------------------------------------
property_id = st.query_params.get("property_id")

if not property_id:
    st.error("No property selected.")
    st.page_link("pages/1_Deal_Feed.py", label="← Back to Deal Feed")
    st.stop()

# ---------------------------------------------------------------------------
# Load data
# ---------------------------------------------------------------------------
try:
    prop = load_property_by_id(property_id)
except Exception as e:
    st.error(f"Could not load property: {e}")
    st.page_link("pages/1_Deal_Feed.py", label="← Back to Deal Feed")
    st.stop()

if not prop:
    st.error(f"Property `{property_id}` not found.")
    st.page_link("pages/1_Deal_Feed.py", label="← Back to Deal Feed")
    st.stop()

try:
    deal = load_deal_by_property(property_id)
except Exception as e:
    st.warning(f"Could not load pipeline status: {e}")
    deal = None

# ---------------------------------------------------------------------------
# Back button
# ---------------------------------------------------------------------------
st.page_link("pages/1_Deal_Feed.py", label="← Back to Deal Feed")

st.divider()

# ---------------------------------------------------------------------------
# Header: address, borough, deal type badge, pipeline status
# ---------------------------------------------------------------------------
deal_type = prop.get("deal_type", "")
icon = DEAL_ICONS.get(deal_type, "⚪")
deal_label = DEAL_LABELS.get(deal_type, deal_type.replace("_", " ").title())
borough = prop.get("borough", "")
address = prop.get("address", "Unknown address")
pipeline_status = deal.get("status") if deal else None

header_col, badge_col = st.columns([3, 1])
with header_col:
    st.title(address)
    st.caption(borough)
with badge_col:
    st.markdown(f"**{icon} {deal_label}**")
    if pipeline_status:
        st.markdown(f"**{PIPELINE_LABELS.get(pipeline_status, pipeline_status)}**")

# ---------------------------------------------------------------------------
# Key metrics row
# ---------------------------------------------------------------------------
price = prop.get("price")
sqft = prop.get("sqft")
price_per_sqft = prop.get("price_per_sqft") or (
    round(price / sqft, 0) if price and sqft else None
)
beds = prop.get("bedrooms")
baths = prop.get("bathrooms")
year_built = prop.get("year_built")
building_class = prop.get("building_class")
tax_class_code = prop.get("tax_class_code")

m1, m2, m3, m4, m5, m6 = st.columns(6)
m1.metric("Price", f"${price:,.0f}" if price else "—")
m2.metric("Price/sqft", f"${price_per_sqft:,.0f}" if price_per_sqft else "—")
m3.metric("Sqft", f"{sqft:,}" if sqft else "—")
m4.metric("Beds", int(beds) if beds is not None else "—")
m5.metric("Baths", f"{baths:g}" if baths is not None else "—")
m6.metric("Year Built", year_built if year_built else "—")

# Tax lien fields — only shown when present (tax_lien deal type)
if building_class or tax_class_code:
    tc1, tc2, tc3 = st.columns([1, 1, 4])
    tc1.metric("Bldg Class", building_class if building_class else "—")
    tc2.metric("Tax Class", tax_class_code if tax_class_code else "—")

st.divider()

# ---------------------------------------------------------------------------
# Map + Notes side by side
# ---------------------------------------------------------------------------
map_col, notes_col = st.columns([3, 2])

with map_col:
    lat = prop.get("lat")
    lng = prop.get("lng")
    if lat and lng:
        df = pd.DataFrame([{"lat": float(lat), "lng": float(lng), "address": address}])
        layer = pdk.Layer(
            "ScatterplotLayer",
            data=df,
            get_position=["lng", "lat"],
            get_fill_color=[220, 38, 38, 220],
            get_radius=80,
            radius_min_pixels=8,
            radius_max_pixels=24,
            pickable=True,
        )
        view = pdk.ViewState(latitude=float(lat), longitude=float(lng), zoom=15, pitch=0)
        st.pydeck_chart(
            pdk.Deck(
                layers=[layer],
                initial_view_state=view,
                tooltip={"html": "<b>{address}</b>", "style": {"color": "white"}},
                map_style="https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json",
            )
        )
    else:
        st.info("No coordinates available for this property.")

with notes_col:
    st.subheader("Notes")
    current_notes = deal.get("notes") or "" if deal else ""
    new_notes = st.text_area(
        "Notes",
        value=current_notes,
        height=180,
        label_visibility="collapsed",
        placeholder="Add your analysis notes here…",
    )
    if deal:
        if st.button("Save Notes", use_container_width=True):
            try:
                update_deal_notes(deal["id"], new_notes)
                st.success("Notes saved.")
            except Exception as e:
                st.error(f"Failed to save notes: {e}")
        st.divider()
        st.caption("**Pipeline Stage**")
        new_status = st.selectbox(
            "Stage",
            options=STAGES,
            index=STAGES.index(pipeline_status) if pipeline_status in STAGES else 0,
            format_func=lambda s: PIPELINE_LABELS.get(s, s),
            label_visibility="collapsed",
        )
        if new_status != pipeline_status:
            update_deal_status(deal["id"], new_status)
            st.rerun()
    else:
        if st.button("+ Add to Pipeline", use_container_width=True):
            try:
                add_to_pipeline(property_id, "watching")
                st.rerun()
            except Exception as e:
                st.error(f"Failed to add to pipeline: {e}")

st.divider()

# ---------------------------------------------------------------------------
# Walk / Transit / Bike Scores (TES-47)
# ---------------------------------------------------------------------------
walk_score = prop.get("walk_score")
transit_score = prop.get("transit_score")
bike_score = prop.get("bike_score")

_has_scores = any(v is not None for v in [walk_score, transit_score, bike_score])

def _score_color(score: int | None) -> str:
    if score is None:
        return "#6b7280"
    if score >= 90:
        return "#15803d"
    if score >= 70:
        return "#16a34a"
    if score >= 50:
        return "#ca8a04"
    return "#dc2626"

def _score_badge(label: str, score: int | None, icon: str) -> str:
    color = _score_color(score)
    val = str(score) if score is not None else "—"
    return (
        f"<div style='text-align:center'>"
        f"<div style='font-size:2rem;font-weight:700;color:{color}'>{val}</div>"
        f"<div style='font-size:0.75rem;color:#9ca3af'>{icon} {label}</div>"
        f"</div>"
    )

with st.expander("🚶 Walkability Scores", expanded=_has_scores):
    if not _has_scores:
        st.info(
            "No walkability scores yet. Run `python data/enrich_walk_score.py` "
            "after adding your `WALKSCORE_API_KEY` to `.env`."
        )
    else:
        ws1, ws2, ws3 = st.columns(3)
        ws1.markdown(_score_badge("Walk Score", walk_score, "🚶"), unsafe_allow_html=True)
        ws2.markdown(_score_badge("Transit Score", transit_score, "🚇"), unsafe_allow_html=True)
        ws3.markdown(_score_badge("Bike Score", bike_score, "🚴"), unsafe_allow_html=True)
        st.caption("Scores 0–100 · 90+ Walker's/Rider's/Biker's Paradise · 70–89 Very walkable/bikeable · 50–69 Somewhat · <50 Car-dependent · Powered by [Walk Score](https://www.walkscore.com/)")

st.divider()

# ---------------------------------------------------------------------------
# Parcel & Market Data (PLUTO + ACRIS enrichment — TES-37)
# ---------------------------------------------------------------------------
assessed_value = prop.get("assessed_value")
market_value = prop.get("market_value")
num_units = prop.get("num_units")
num_floors = prop.get("num_floors")
land_use = prop.get("land_use")
zoning_district = prop.get("zoning_district")
last_sale_price = prop.get("last_sale_price")
last_sale_date = prop.get("last_sale_date")
bbl = prop.get("bbl")

_has_pluto = any(v is not None for v in [assessed_value, market_value, num_units, num_floors, land_use, zoning_district])
_has_sale = any(v is not None for v in [last_sale_price, last_sale_date])

with st.expander("🏛 Parcel & Market Data", expanded=_has_pluto or _has_sale):
    if not _has_pluto and not _has_sale:
        st.info(
            "No parcel data yet. Run `python data/backfill_bbl.py` then "
            "`python data/enrich_pluto.py` and `python data/enrich_last_sale.py` to populate."
        )
    else:
        # Row 1: assessed / market / last sale value metrics
        p1, p2, p3, p4 = st.columns(4)
        p1.metric("Assessed Value", f"${assessed_value:,.0f}" if assessed_value else "—")
        p2.metric("Market Value (DOF)", f"${market_value:,.0f}" if market_value else "—")
        p3.metric("Last Sale Price", f"${last_sale_price:,.0f}" if last_sale_price else "—")
        p4.metric("Last Sale Date", last_sale_date[:10] if last_sale_date else "—")

        st.divider()

        # Row 2: parcel details
        d1, d2, d3, d4, d5 = st.columns(5)
        d1.markdown(f"**BBL**  \n`{bbl}`" if bbl else "**BBL**  \n—")
        d2.markdown(f"**Zoning**  \n{zoning_district}" if zoning_district else "**Zoning**  \n—")
        d3.markdown(f"**Land Use**  \n{land_use}" if land_use else "**Land Use**  \n—")
        d4.markdown(f"**Units (Res)**  \n{num_units:,}" if num_units is not None else "**Units (Res)**  \n—")
        d5.markdown(f"**Floors**  \n{num_floors}" if num_floors is not None else "**Floors**  \n—")

st.divider()

# ---------------------------------------------------------------------------
# Lien History (TES-40)
# ---------------------------------------------------------------------------
try:
    lien_records = load_lien_history_by_property(property_id)
except Exception as e:
    lien_records = []
    st.warning(f"Could not load lien history: {e}")

lien_count = len(lien_records)
repeat_offender = lien_count >= 3

lien_label = f"📋 Lien History ({lien_count})" if lien_count else "📋 Lien History"
with st.expander(lien_label, expanded=lien_count > 0):
    if not lien_records:
        st.info(
            "No prior lien history found. "
            "Run `python data/ingest_lien_history.py` to populate lien history for tax lien properties."
        )
    else:
        # Summary row
        lh1, lh2 = st.columns([1, 3])
        lh1.metric("Total Lien Notices", lien_count)
        if repeat_offender:
            lh2.error("⚠️ Repeat offender — 3 or more prior lien notices on this parcel")
        else:
            lh2.success("✅ No repeat-offender flag (fewer than 3 prior notices)")

        st.divider()

        # Timeline table
        rows_display = []
        for r in lien_records:
            rows_display.append({
                "Notice Month": r.get("notice_month") or "—",
                "Lien Cycle": r.get("lien_cycle") or "—",
                "Tax Class": r.get("tax_class") or "—",
                "Bldg Class": r.get("building_class") or "—",
                "Water Only": "Yes" if r.get("water_debt_only") else "No",
                "Amount": f"${r['lien_amount']:,.0f}" if r.get("lien_amount") else "—",
            })

        df_liens = pd.DataFrame(rows_display)
        st.dataframe(df_liens, use_container_width=True, hide_index=True)

st.divider()

# ---------------------------------------------------------------------------
# Violations
# ---------------------------------------------------------------------------
try:
    violations = load_violations_by_property(property_id)
except Exception as e:
    violations = []
    st.warning(f"Could not load violations: {e}")

hpd_viols = [v for v in violations if v.get("source") == "hpd"]
dob_viols = [v for v in violations if v.get("source") == "dob"]
total_count = len(violations)

expander_label = f"⚠️ Violations ({total_count})" if total_count else "⚠️ Violations"
with st.expander(expander_label, expanded=total_count > 0):
    if not violations:
        st.info("No violations on record.")
    else:
        hpd_tab, dob_tab = st.tabs([
            f"HPD ({len(hpd_viols)})",
            f"DOB ({len(dob_viols)})",
        ])

        with hpd_tab:
            if not hpd_viols:
                st.info("No HPD violations.")
            else:
                for v in hpd_viols:
                    with st.container(border=True):
                        c1, c2, c3 = st.columns([1, 2, 1])
                        vtype = v.get("violation_type") or "?"
                        c1.markdown(f"**Class {vtype}**")
                        status = v.get("status") or ""
                        c2.caption(f"{'🔴' if status == 'Open' else '🟢'} {status}")
                        c3.caption(v.get("issued_date") or "")
                        if v.get("description"):
                            st.caption(v["description"])

        with dob_tab:
            if not dob_viols:
                st.info("No DOB violations.")
            else:
                for v in dob_viols:
                    with st.container(border=True):
                        vtype = v.get("violation_type") or ""
                        status = v.get("status") or ""
                        c1, c2 = st.columns([3, 1])
                        if vtype:
                            c1.markdown(f"**{vtype}**")
                        c2.caption(f"{'🔴' if status == 'Open' else '🟢'} {status}")
                        if v.get("issued_date"):
                            st.caption(f"Issued: {v['issued_date']}")
                        if v.get("description"):
                            st.caption(v["description"])

# ---------------------------------------------------------------------------
# Sale History (ACRIS — TES-45)
# ---------------------------------------------------------------------------
try:
    sale_history = load_sale_history(property_id)
except Exception as _e:
    sale_history = []
    st.warning(f"Could not load sale history: {_e}")

_sh_count = len(sale_history)
_sh_label = f"📋 Sale History ({_sh_count})" if _sh_count else "📋 Sale History"
with st.expander(_sh_label, expanded=_sh_count > 0):
    if not sale_history:
        st.info("No deed sales found in ACRIS for this property.")
    else:
        # Most recent sale summary metrics
        _recent = sale_history[0]  # already sorted DESC by sale_date
        _rp = _recent.get("sale_price")
        _rd = _recent.get("sale_date")
        sh1, sh2, sh3 = st.columns(3)
        sh1.metric("Last Sold", f"${_rp:,.0f}" if _rp else "—")
        sh2.metric("Sale Date", _rd[:10] if _rd else "—")
        sh3.metric("Buyer", _recent.get("buyer_name") or "—")

        if _sh_count > 1:
            st.divider()
            st.caption("**Full Sale History**")

        # Build and display full history table
        _rows = []
        for _s in sale_history:
            _sd = _s.get("sale_date")
            _sp = _s.get("sale_price")
            _rows.append({
                "Date": _sd[:10] if _sd else "—",
                "Price": f"${_sp:,.0f}" if _sp else "—",
                "Doc Type": _s.get("doc_type") or "—",
                "Buyer": _s.get("buyer_name") or "—",
                "Seller": _s.get("seller_name") or "—",
            })
        st.dataframe(pd.DataFrame(_rows), use_container_width=True, hide_index=True)

st.divider()

# ---------------------------------------------------------------------------
# Deal Calculator (TES-21)
# ---------------------------------------------------------------------------
with st.expander("🧮 Quick Deal Calculator", expanded=True):
    st.caption("Fix-and-flip analysis based on the 70% rule")

    inp_left, inp_right = st.columns(2)

    with inp_left:
        calc_purchase = st.number_input(
            "Purchase Price ($)",
            min_value=0,
            max_value=50_000_000,
            value=int(price) if price else 0,
            step=10_000,
            format="%d",
            key="calc_purchase",
        )
        calc_reno = st.slider(
            "Renovation Budget ($)",
            min_value=0,
            max_value=500_000,
            value=50_000,
            step=5_000,
            format="$%d",
            key="calc_reno",
        )
        calc_arv = st.number_input(
            "After-Repair Value / ARV ($)",
            min_value=0,
            max_value=50_000_000,
            value=int(price * 1.3) if price else 0,
            step=10_000,
            format="%d",
            key="calc_arv",
        )

    # Core calculations
    max_offer = calc_arv * 0.70 - calc_reno if calc_arv else 0
    total_cost = calc_purchase + calc_reno
    projected_profit = calc_arv - total_cost if calc_arv else 0
    roi = (projected_profit / total_cost * 100) if total_cost > 0 and calc_arv else 0

    with inp_right:
        st.metric("Max Offer (70% Rule)", f"${max_offer:,.0f}" if calc_arv else "—")
        st.metric(
            "Projected Profit",
            f"${projected_profit:,.0f}" if calc_arv else "—",
            delta=f"{roi:.1f}% ROI" if calc_arv and total_cost else None,
        )
        st.metric("Total Investment", f"${total_cost:,.0f}")

    # Verdict
    if calc_arv and calc_purchase:
        if calc_purchase <= max_offer:
            st.success(
                f"✅ **Good deal** — purchase price is "
                f"${max_offer - calc_purchase:,.0f} under the 70% max offer."
            )
        elif calc_purchase <= max_offer * 1.15:
            st.warning(
                f"⚠️ **Borderline** — purchase price is "
                f"${calc_purchase - max_offer:,.0f} above the 70% max offer."
            )
        else:
            st.error(
                f"❌ **Over max offer** — exceeds 70% rule by "
                f"${calc_purchase - max_offer:,.0f}."
            )

    st.divider()

    # Financing
    st.caption("**Financing (optional)**")
    fin1, fin2, fin3 = st.columns(3)
    with fin1:
        down_pct = st.slider("Down Payment %", 10, 50, 20, step=5, key="calc_down")
    with fin2:
        interest_rate = st.slider("Interest Rate %", 4.0, 15.0, 7.5, step=0.25, key="calc_rate")
    with fin3:
        loan_term = st.selectbox(
            "Loan Term",
            [12, 18, 24, 36],
            format_func=lambda x: f"{x} months",
            key="calc_term",
        )

    if calc_purchase > 0:
        loan_amount = calc_purchase * (1 - down_pct / 100)
        down_payment = calc_purchase * (down_pct / 100)
        monthly_rate = (interest_rate / 100) / 12
        n = loan_term
        if monthly_rate > 0:
            monthly_payment = (
                loan_amount * (monthly_rate * (1 + monthly_rate) ** n)
                / ((1 + monthly_rate) ** n - 1)
            )
        else:
            monthly_payment = loan_amount / n
        total_interest = monthly_payment * loan_term - loan_amount
        total_cash_in = down_payment + calc_reno

        fc1, fc2, fc3 = st.columns(3)
        fc1.metric("Monthly Payment", f"${monthly_payment:,.0f}")
        fc2.metric("Total Interest", f"${total_interest:,.0f}")
        fc3.metric("Cash In (down + reno)", f"${total_cash_in:,.0f}")

        if calc_arv and projected_profit > 0 and total_cash_in > 0:
            coc = (projected_profit - total_interest) / total_cash_in * 100
            st.metric("Cash-on-Cash Return", f"{coc:.1f}%")
