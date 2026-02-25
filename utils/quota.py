"""API quota tracker — enforces monthly call limits per external API.

Stores call counts in the Supabase `api_quota` table, keyed by
(api_name, year_month). Before each external API call, scripts should
call `check_and_increment()` which:

  1. Returns the current usage for the month.
  2. Raises QuotaExceededError if the limit would be exceeded.
  3. Atomically increments the counter on success.

Usage in enrichment scripts:

    from utils.quota import check_and_increment, QuotaExceededError

    try:
        usage = check_and_increment("rentcast", monthly_limit=50)
        log.info("RentCast quota: %d/50 used this month", usage)
    except QuotaExceededError as e:
        log.error(str(e))
        sys.exit(1)

    # ... make the API call ...

Call `get_usage()` to inspect without incrementing (e.g. at script start).
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from utils.supabase_client import get_client  # noqa: E402

log = logging.getLogger(__name__)


class QuotaExceededError(Exception):
    """Raised when a monthly API quota would be exceeded."""


def _year_month() -> str:
    """Return current UTC month as 'YYYY-MM'."""
    return datetime.now(timezone.utc).strftime("%Y-%m")


def get_usage(api_name: str) -> tuple[int, int | None]:
    """Return (call_count, monthly_limit) for the current month.

    Returns (0, None) if no record exists yet for this month.
    """
    client = get_client()
    ym = _year_month()
    result = (
        client.table("api_quota")
        .select("call_count,monthly_limit")
        .eq("api_name", api_name)
        .eq("year_month", ym)
        .execute()
    )
    if result.data:
        row = result.data[0]
        return row["call_count"], row["monthly_limit"]
    return 0, None


def check_and_increment(api_name: str, monthly_limit: int = 50) -> int:
    """Check quota and increment counter. Returns new call_count.

    Raises QuotaExceededError if adding one more call would exceed
    monthly_limit. Safe to call before every API request.
    """
    client = get_client()
    ym = _year_month()

    # Upsert: insert row if it doesn't exist, then read current count
    result = (
        client.table("api_quota")
        .upsert(
            {"api_name": api_name, "year_month": ym, "monthly_limit": monthly_limit},
            on_conflict="api_name,year_month",
            ignore_duplicates=True,
        )
        .execute()
    )

    # Read current count
    row = (
        client.table("api_quota")
        .select("call_count,monthly_limit")
        .eq("api_name", api_name)
        .eq("year_month", ym)
        .execute()
        .data[0]
    )
    current = row["call_count"]
    limit = row["monthly_limit"]

    if current >= limit:
        raise QuotaExceededError(
            f"{api_name} monthly quota exhausted: {current}/{limit} calls used "
            f"in {ym}. Upgrade your plan or wait until next month."
        )

    # Increment
    new_count = current + 1
    client.table("api_quota").update({"call_count": new_count}).eq(
        "api_name", api_name
    ).eq("year_month", ym).execute()

    log.info("%s quota: %d/%d calls used in %s", api_name, new_count, limit, ym)
    return new_count


def log_usage_summary(api_name: str) -> None:
    """Log a human-readable quota summary at script start."""
    count, limit = get_usage(api_name)
    ym = _year_month()
    if limit is None:
        log.info("%s quota: 0 calls used in %s (no record yet)", api_name, ym)
    else:
        remaining = limit - count
        log.info(
            "%s quota: %d/%d calls used in %s (%d remaining)",
            api_name, count, limit, ym, remaining,
        )
