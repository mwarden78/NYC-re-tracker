"""Shared Supabase data loaders for the Streamlit app.

All functions use @st.cache_data so results are cached per session and
only re-fetched when the TTL expires. Import these from any page module.
"""

from __future__ import annotations

import sys
import os

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
