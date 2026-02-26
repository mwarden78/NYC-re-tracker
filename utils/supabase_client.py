"""Supabase client singleton."""

from functools import lru_cache
from supabase import create_client, Client
from utils.config import get_supabase_url, get_supabase_key


@lru_cache(maxsize=1)
def get_client() -> Client:
    """Return a cached Supabase client instance."""
    return create_client(get_supabase_url(), get_supabase_key())


def fetch_all_rows(query_fn, page_size: int = 1000) -> list[dict]:
    """Fetch every row from a Supabase query, paginating past the 1000-row API limit.

    Pass a *callable* that returns a fresh query builder each time — do NOT pass
    a pre-built query object, because supabase-py's QueryBuilder mutates in place
    and accumulates params across calls.

    Usage:
        rows = fetch_all_rows(
            lambda: client.table("properties").select("id,bbl").not_.is_("bbl", "null")
        )

    The callable must NOT call .execute() or .range(); this function applies
    .range() and .execute() internally on a fresh builder each page.
    """
    all_rows: list[dict] = []
    offset = 0
    while True:
        batch = query_fn().range(offset, offset + page_size - 1).execute().data or []
        all_rows.extend(batch)
        if len(batch) < page_size:
            break
        offset += page_size
    return all_rows
