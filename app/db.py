"""Shared Supabase data loaders for the Streamlit app.

All functions use @st.cache_data so results are cached per session and
only re-fetched when the TTL expires. Import these from any page module.
"""

from __future__ import annotations

import sys
import os
from datetime import datetime, timezone as _tz

import streamlit as st

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from utils.supabase_client import get_client  # noqa: E402

_TTL = 300  # seconds — refresh data every 5 minutes


@st.cache_data(ttl=_TTL)
def load_properties(
    borough: str | None = None,
    deal_type: str | None = None,
    limit: int = 500,
) -> list[dict]:
    """Return properties from Supabase, optionally filtered."""
    client = get_client()
    query = client.table("properties").select("*").order("created_at", desc=True).limit(limit)
    if borough:
        query = query.eq("borough", borough)
    if deal_type:
        query = query.eq("deal_type", deal_type)
    result = query.execute()
    return result.data or []


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
