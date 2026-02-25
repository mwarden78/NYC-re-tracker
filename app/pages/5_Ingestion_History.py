"""Ingestion History — data coverage and enrichment stats (TES-65)."""

from __future__ import annotations

from datetime import datetime, timezone

import streamlit as st
from db import load_ingestion_stats

st.set_page_config(
    page_title="Ingestion History | NYC RE Tracker",
    page_icon="📥",
    layout="wide",
)

st.title("Ingestion History")
st.caption("Data coverage and enrichment status across all ingested properties")

# ---------------------------------------------------------------------------
# Load stats
# ---------------------------------------------------------------------------
try:
    stats = load_ingestion_stats()
except Exception as e:
    st.error(f"Could not load stats from Supabase. Check your `.env` file. ({e})")
    st.stop()

total = stats["total_properties"]

# ---------------------------------------------------------------------------
# Top-level summary
# ---------------------------------------------------------------------------
st.subheader("Overview")

col1, col2, col3, col4 = st.columns(4)
col1.metric("Total Properties", f"{total:,}")
col2.metric("Added Last 7 Days", f"{stats['new_7d']:,}")
col3.metric("Added Last 30 Days", f"{stats['new_30d']:,}")

if stats["latest_ingested"]:
    try:
        ts = datetime.fromisoformat(stats["latest_ingested"].replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        delta = now - ts
        if delta.days == 0:
            age_label = "Today"
        elif delta.days == 1:
            age_label = "Yesterday"
        else:
            age_label = f"{delta.days:,}d ago"
        col4.metric("Last Ingested", age_label, help=ts.strftime("%Y-%m-%d %H:%M UTC"))
    except Exception:
        col4.metric("Last Ingested", stats["latest_ingested"])
else:
    col4.metric("Last Ingested", "—")

st.divider()

# ---------------------------------------------------------------------------
# Source breakdown
# ---------------------------------------------------------------------------
st.subheader("Properties by Source")

by_source = stats["by_source"]
if by_source and total:
    SOURCE_LABELS = {
        "nyc_open_data": "NYC Open Data",
        "manual": "Manual Entry",
        "zillow": "Zillow",
        "unknown": "Unknown",
    }
    cols = st.columns(len(by_source))
    for i, (src, cnt) in enumerate(sorted(by_source.items(), key=lambda x: -x[1])):
        label = SOURCE_LABELS.get(src, src)
        pct = cnt / total * 100
        cols[i].metric(label, f"{cnt:,}", delta=f"{pct:.1f}%", delta_color="off")
else:
    st.caption("No properties ingested yet.")

st.divider()

# ---------------------------------------------------------------------------
# Enrichment coverage
# ---------------------------------------------------------------------------
st.subheader("Enrichment Coverage")
st.caption(
    "Shows how many of the total properties have been enriched by each data pipeline."
)

if total == 0:
    st.info("No properties found — run the ingestion script to populate the database.")
else:
    ENRICHMENTS = [
        ("BBL Matched", "with_bbl", "Geocoded borough/block/lot identifier — required for all downstream enrichment (backfill_bbl.py)"),
        ("PLUTO Enriched", "pluto_enriched", "MapPLUTO fields: assessed value, market value, units, zoning (enrich_pluto.py)"),
        ("ACRIS Sale Data", "sale_enriched", "Most recent deed sale price & date from ACRIS (enrich_last_sale.py)"),
        ("Lien Amount", "lien_amount", "Most recent tax lien document amount from ACRIS (enrich_lien_amount.py)"),
        ("Tax Bills", "tax_bills", "DOF property tax arrears and annual tax charges (ingest_tax_bills.py)"),
        ("Walk Score", "walk_score", "Walk / Transit / Bike scores from walkscore.com (enrich_walk_score.py)"),
    ]

    for label, key, help_text in ENRICHMENTS:
        count = stats[key]
        pct = count / total * 100 if total else 0
        missing = total - count
        col_label, col_bar, col_nums = st.columns([2, 5, 2])
        with col_label:
            st.write(f"**{label}**")
            st.caption(help_text)
        with col_bar:
            st.progress(pct / 100, text=f"{pct:.1f}%")
        with col_nums:
            st.metric(
                "Enriched / Total",
                f"{count:,} / {total:,}",
                delta=f"{missing:,} missing",
                delta_color="inverse" if missing > 0 else "off",
            )

st.divider()

# ---------------------------------------------------------------------------
# Related table row counts
# ---------------------------------------------------------------------------
st.subheader("Related Tables")

col1, col2, col3 = st.columns(3)
col1.metric(
    "Sale History Records",
    f"{stats['sale_history_total']:,}",
    help="Rows in `sale_history` table — deed records from ACRIS (enrich_last_sale.py)",
)
col2.metric(
    "Violation Records",
    f"{stats['violations_total']:,}",
    help="Rows in `violations` table — HPD and DOB violations (ingest_violations.py)",
)
col3.metric(
    "Lien History Records",
    f"{stats['lien_history_total']:,}",
    help="Rows in `lien_history` table — historical DOF tax lien notices (ingest_lien_history.py)",
)

st.divider()

# ---------------------------------------------------------------------------
# Scripts reference
# ---------------------------------------------------------------------------
with st.expander("Ingestion & Enrichment Scripts"):
    st.markdown(
        """
| Script | Description | Key Table(s) |
|--------|-------------|-------------|
| `data/ingest_nyc_open_data.py` | Ingests foreclosure and tax lien properties from NYC Open Data | `properties` |
| `data/backfill_bbl.py` | Geocodes addresses → BBL using the NYC GeoSearch API | `properties.bbl` |
| `data/backfill_geocoding.py` | Geocodes missing lat/lng values | `properties.lat`, `.lng` |
| `data/enrich_pluto.py` | Adds MapPLUTO fields (assessed value, zoning, units) | `properties` |
| `data/enrich_last_sale.py` | Adds most recent ACRIS deed sale data | `properties`, `sale_history` |
| `data/enrich_lien_amount.py` | Adds most recent ACRIS lien document amount | `properties` |
| `data/ingest_tax_bills.py` | Adds DOF property tax charges and arrears | `properties` |
| `data/ingest_violations.py` | Ingests HPD and DOB violations per property | `violations` |
| `data/ingest_lien_history.py` | Ingests historical DOF tax lien notices | `lien_history` |
| `data/enrich_walk_score.py` | Adds Walk / Transit / Bike scores | `properties` |
"""
    )
