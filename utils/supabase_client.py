"""Supabase client singleton."""

from functools import lru_cache
from supabase import create_client, Client
from utils.config import get_supabase_url, get_supabase_key


@lru_cache(maxsize=1)
def get_client() -> Client:
    """Return a cached Supabase client instance."""
    return create_client(get_supabase_url(), get_supabase_key())


def fetch_all_rows(query, page_size: int = 1000) -> list[dict]:
    """Fetch every row from a Supabase query, paginating past the 1000-row API limit.

    Usage:
        query = client.table("properties").select("id,bbl").not_.is_("bbl", "null")
        rows = fetch_all_rows(query)

    The caller must NOT call .execute() or .limit() before passing the query
    object in — this function applies .range() and .execute() internally.
    """
    all_rows: list[dict] = []
    offset = 0
    while True:
        batch = query.range(offset, offset + page_size - 1).execute().data or []
        all_rows.extend(batch)
        if len(batch) < page_size:
            break
        offset += page_size
    return all_rows
