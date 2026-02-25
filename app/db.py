"""Shared Supabase data loaders for the Streamlit app.

All functions use @st.cache_data so results are cached per session and
only re-fetched when the TTL expires. Import these from any page module.
"""

from __future__ import annotations

import sys
import os
from collections import Counter
from datetime import datetime, timedelta, timezone as _tz

import streamlit as st

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from utils.supabase_client import get_client, fetch_all_rows  # noqa: E402

_TTL = 300  # seconds — refresh data every 5 minutes


@st.cache_data(ttl=_TTL)
def load_properties(
    borough: str | None = None,
    deal_type: str | None = None,
) -> list[dict]:
    """Return all properties from Supabase, paginating past the 1k-row API limit."""
    client = get_client()
    PAGE = 1000
    all_rows: list[dict] = []
    offset = 0
    while True:
        query = (
            client.table("properties")
            .select("*")
            .order("created_at", desc=True)
            .range(offset, offset + PAGE - 1)
        )
        if borough:
            query = query.eq("borough", borough)
        if deal_type:
            query = query.eq("deal_type", deal_type)
        result = query.execute()
        batch = result.data or []
        all_rows.extend(batch)
        if len(batch) < PAGE:
            break
        offset += PAGE
    return all_rows


@st.cache_data(ttl=_TTL)
def load_deals() -> list[dict]:
    """Return all deals joined with their property."""
    client = get_client()
    result = (
        client.table("deals")
        .select("*, properties(*)")
        .order("created_at", desc=True)
        .execute()
    )
    return result.data or []


def add_to_pipeline(property_id: str, status: str = "watching") -> None:
    """Insert a deal record and clear the cache so the UI refreshes."""
    client = get_client()
    client.table("deals").insert({"property_id": property_id, "status": status}).execute()
    st.cache_data.clear()


def update_deal_status(deal_id: str, new_status: str) -> None:
    """Update a deal's pipeline status and clear the cache."""
    client = get_client()
    client.table("deals").update({"status": new_status}).eq("id", deal_id).execute()
    st.cache_data.clear()


@st.cache_data(ttl=_TTL)
def load_property_by_id(property_id: str) -> dict | None:
    """Return a single property by ID, or None if not found."""
    client = get_client()
    result = (
        client.table("properties")
        .select("*")
        .eq("id", property_id)
        .limit(1)
        .execute()
    )
    return result.data[0] if result.data else None


def load_deal_by_property(property_id: str) -> dict | None:
    """Return the deal for a property, or None if not tracked."""
    client = get_client()
    result = (
        client.table("deals")
        .select("*")
        .eq("property_id", property_id)
        .limit(1)
        .execute()
    )
    return result.data[0] if result.data else None


def update_deal_notes(deal_id: str, notes: str) -> None:
    """Update a deal's notes and clear the cache."""
    client = get_client()
    client.table("deals").update({"notes": notes}).eq("id", deal_id).execute()
    st.cache_data.clear()


@st.cache_data(ttl=_TTL)
def load_sale_history(property_id: str) -> list[dict]:
    """Return sale_history rows for a property, ordered by sale_date DESC."""
    client = get_client()
    result = (
        client.table("sale_history")
        .select("*")
        .eq("property_id", property_id)
        .order("sale_date", desc=True)
        .execute()
    )
    return result.data or []


@st.cache_data(ttl=_TTL)
def load_last_sales() -> dict[str, dict]:
    """Return {property_id: {"sale_price": X, "sale_date": "YYYY-MM-DD"}} — most recent sale per property."""
    client = get_client()
    result = (
        client.table("sale_history")
        .select("property_id,sale_price,sale_date")
        .not_.is_("sale_date", "null")
        .order("sale_date", desc=True)
        .execute()
    )
    # Walk the DESC-ordered rows and keep only the first (most recent) per property
    best: dict[str, dict] = {}
    for row in (result.data or []):
        pid = row["property_id"]
        if pid not in best:
            best[pid] = {"sale_price": row.get("sale_price"), "sale_date": row.get("sale_date")}
    return best


@st.cache_data(ttl=_TTL)
def load_violations_by_property(property_id: str) -> list[dict]:
    """Return all violations for a single property, ordered by issued_date desc."""
    client = get_client()
    result = (
        client.table("violations")
        .select("*")
        .eq("property_id", property_id)
        .order("issued_date", desc=True)
        .execute()
    )
    return result.data or []


@st.cache_data(ttl=_TTL)
def load_lien_history_by_property(property_id: str) -> list[dict]:
    """Return prior lien history for a single property, newest notice first."""
    client = get_client()
    result = (
        client.table("lien_history")
        .select("*")
        .eq("property_id", property_id)
        .order("notice_month", desc=True)
        .execute()
    )
    return result.data or []


@st.cache_data(ttl=_TTL)
def load_hpd_registration(property_id: str) -> list[dict]:
    """Return HPD registration rows for a property, most recent first (Active before Terminated)."""
    client = get_client()
    result = (
        client.table("hpd_registrations")
        .select("*")
        .eq("property_id", property_id)
        .order("registration_end_date", desc=True)
        .execute()
    )
    rows = result.data or []
    # Sort: Active records first, then Terminated
    rows.sort(key=lambda r: (r.get("lifecycle_stage") != "Active", r.get("registration_end_date") or ""))
    return rows


@st.cache_data(ttl=_TTL)
def load_violation_counts(open_only: bool = False) -> dict[str, int]:
    """Return {property_id: violation_count} for all properties with violations.

    Args:
        open_only: If True, count only violations with status='Open'.
    """
    client = get_client()
    query = client.table("violations").select("property_id")
    if open_only:
        query = query.eq("status", "Open")
    result = query.execute()
    counts: dict[str, int] = {}
    for row in (result.data or []):
        pid = row["property_id"]
        counts[pid] = counts.get(pid, 0) + 1
    return counts


@st.cache_data(ttl=_TTL)
def load_saved_searches() -> list[dict]:
    """Return all saved searches, ordered newest first."""
    client = get_client()
    result = (
        client.table("saved_searches")
        .select("*")
        .order("created_at", desc=True)
        .execute()
    )
    return result.data or []


def save_search(name: str, filters: dict) -> dict:
    """Insert a saved search and clear the cache."""
    client = get_client()
    result = (
        client.table("saved_searches")
        .insert({"name": name, "filters": filters})
        .execute()
    )
    st.cache_data.clear()
    return result.data[0]


def delete_saved_search(search_id: str) -> None:
    """Delete a saved search by ID and clear the cache."""
    client = get_client()
    client.table("saved_searches").delete().eq("id", search_id).execute()
    st.cache_data.clear()


def mark_search_checked(search_id: str) -> None:
    """Update last_checked_at to now for the given saved search."""
    client = get_client()
    client.table("saved_searches").update(
        {"last_checked_at": datetime.now(_tz.utc).isoformat()}
    ).eq("id", search_id).execute()
    st.cache_data.clear()


def count_new_matches(search: dict, all_properties: list[dict]) -> int:
    """Count properties that match search filters and were created after last_checked_at."""
    filters = search.get("filters", {})
    last_checked = search.get("last_checked_at")
    if not last_checked:
        return 0
    try:
        lc_dt = datetime.fromisoformat(last_checked.replace("Z", "+00:00"))
    except Exception:
        return 0

    count = 0
    for p in all_properties:
        created = p.get("created_at")
        if not created:
            continue
        try:
            p_dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
        except Exception:
            continue
        if p_dt <= lc_dt:
            continue

        if filters.get("boroughs") and p.get("borough") not in filters["boroughs"]:
            continue
        if filters.get("deal_types") and p.get("deal_type") not in filters["deal_types"]:
            continue
        if filters.get("prop_types") and p.get("property_type") not in filters["prop_types"]:
            continue
        price = p.get("price")
        if filters.get("price_min") is not None and price is not None and price < filters["price_min"]:
            continue
        if filters.get("price_max") is not None and price is not None and price > filters["price_max"]:
            continue
        if filters.get("min_beds"):
            beds = p.get("bedrooms")
            if beds is None or int(beds) < filters["min_beds"]:
                continue
        count += 1
    return count


@st.cache_data(ttl=_TTL)
def load_summary() -> dict:
    """Return aggregate counts for the home page dashboard."""
    properties = load_properties()
    deals = load_deals()

    total = len(properties)
    by_deal_type: dict[str, int] = {}
    by_borough: dict[str, int] = {}

    for p in properties:
        dt = p.get("deal_type", "unknown")
        by_deal_type[dt] = by_deal_type.get(dt, 0) + 1
        b = p.get("borough", "unknown")
        by_borough[b] = by_borough.get(b, 0) + 1

    pipeline: dict[str, int] = {}
    for d in deals:
        s = d.get("status", "unknown")
        pipeline[s] = pipeline.get(s, 0) + 1

    return {
        "total_properties": total,
        "by_deal_type": by_deal_type,
        "by_borough": by_borough,
        "pipeline": pipeline,
        "total_deals": len(deals),
    }


@st.cache_data(ttl=_TTL)
def load_ingestion_stats() -> dict:
    """Return data coverage and ingestion statistics for the Ingestion History page.

    Uses efficient COUNT queries (no full row fetches) for coverage stats.
    """
    client = get_client()

    def _count(table: str, filters: dict | None = None) -> int:
        q = client.table(table).select("id", count="exact")
        for col, val in (filters or {}).items():
            if val is None:
                q = q.not_.is_(col, "null")
            else:
                q = q.eq(col, val)
        return q.execute().count or 0

    total = _count("properties")

    # --- Enrichment coverage ---
    with_bbl = _count("properties", {"bbl": None}) if total else 0
    pluto_enriched = _count("properties", {"assessed_value": None}) if total else 0
    sale_enriched = _count("properties", {"last_sale_price": None}) if total else 0
    walk_score = _count("properties", {"walk_score": None}) if total else 0
    tax_bills = _count("properties", {"annual_tax": None}) if total else 0
    lien_amount = _count("properties", {"lien_amount": None}) if total else 0

    # --- Related table totals ---
    violations_total = _count("violations")
    sale_history_total = _count("sale_history")
    lien_history_total = _count("lien_history")

    # --- Source breakdown (paginated — avoids 1k-row Supabase cap) ---
    source_rows = fetch_all_rows(client.table("properties").select("source"))
    by_source = dict(Counter(r.get("source") or "unknown" for r in source_rows))

    # --- Recent ingestion: properties added in the last 7 / 30 days ---
    now_utc = datetime.now(_tz.utc)
    cutoff_7d = (now_utc - timedelta(days=7)).isoformat()
    cutoff_30d = (now_utc - timedelta(days=30)).isoformat()
    new_7d = (
        client.table("properties")
        .select("id", count="exact")
        .gte("created_at", cutoff_7d)
        .execute()
        .count or 0
    )
    new_30d = (
        client.table("properties")
        .select("id", count="exact")
        .gte("created_at", cutoff_30d)
        .execute()
        .count or 0
    )

    # --- Most recent ingestion timestamp ---
    latest_row = (
        client.table("properties")
        .select("created_at")
        .order("created_at", desc=True)
        .limit(1)
        .execute()
        .data
    )
    latest_ingested = latest_row[0]["created_at"] if latest_row else None

    return {
        "total_properties": total,
        "with_bbl": with_bbl,
        "pluto_enriched": pluto_enriched,
        "sale_enriched": sale_enriched,
        "walk_score": walk_score,
        "tax_bills": tax_bills,
        "lien_amount": lien_amount,
        "violations_total": violations_total,
        "sale_history_total": sale_history_total,
        "lien_history_total": lien_history_total,
        "by_source": by_source,
        "new_7d": new_7d,
        "new_30d": new_30d,
        "latest_ingested": latest_ingested,
    }
