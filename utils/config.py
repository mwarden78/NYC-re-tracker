"""Load and validate environment configuration."""

import os
from dotenv import load_dotenv

load_dotenv()


def get_supabase_url() -> str:
    url = os.environ.get("SUPABASE_URL", "")
    if not url:
        raise ValueError("SUPABASE_URL is not set. Add it to your .env file.")
    return url


def get_supabase_key() -> str:
    key = os.environ.get("SUPABASE_KEY", "")
    if not key:
        raise ValueError("SUPABASE_KEY is not set. Add it to your .env file.")
    return key
