"""Supabase client singleton."""

from functools import lru_cache
from supabase import create_client, Client
from utils.config import get_supabase_url, get_supabase_key


@lru_cache(maxsize=1)
def get_client() -> Client:
    """Return a cached Supabase client instance."""
    return create_client(get_supabase_url(), get_supabase_key())
