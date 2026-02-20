"""Load and validate environment configuration.

Secrets are resolved in this order:
1. Environment variables / .env file (local dev)
2. Streamlit secrets (st.secrets) — used on Streamlit Community Cloud
"""

import os
from dotenv import load_dotenv

load_dotenv()


def _get_secret(key: str) -> str:
    """Return a secret from env vars, falling back to st.secrets."""
    value = os.environ.get(key, "")
    if value:
        return value
    # Streamlit Community Cloud injects secrets via st.secrets
    try:
        import streamlit as st
        return st.secrets.get(key, "")
    except Exception:
        return ""


def get_supabase_url() -> str:
    url = _get_secret("SUPABASE_URL")
    if not url:
        raise ValueError("SUPABASE_URL is not set. Add it to .env or Streamlit secrets.")
    return url


def get_supabase_key() -> str:
    key = _get_secret("SUPABASE_KEY")
    if not key:
        raise ValueError("SUPABASE_KEY is not set. Add it to .env or Streamlit secrets.")
    return key
