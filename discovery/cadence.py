"""
Cadence detection for candidate sources (Part C step 2): given a feed URL,
determine whether it's an ongoing publication at all, and if so whether it
posts daily/weekday or sporadically -- this decides which bucket (Part B's
daily or Sunday list) an approved source would join.

No langgraph imports, no LLM calls.
"""

from __future__ import annotations

from datetime import datetime
from statistics import median

from discovery.parsers.rss_common import fetch_rss_feed

DAILY_MEDIAN_GAP_HOURS = 48  # <= 2 days between posts, on average -> "daily"
MIN_ITEMS_FOR_ONGOING = 3    # fewer than this -> treat as a one-off, not a real candidate


def detect_cadence(feed_url: str, source_name: str = "candidate") -> str | None:
    """Returns "daily", "sporadic", or None if the feed doesn't look like
    an ongoing publication (too few items, or the feed couldn't be fetched
    at all -- e.g. a one-off article page with no real feed)."""
    result = fetch_rss_feed(feed_url, source_name=source_name, limit=20)
    if result.errors or len(result.rows) < MIN_ITEMS_FOR_ONGOING:
        return None

    timestamps = sorted(
        datetime.fromisoformat(row["fetched_at"]) for row in result.rows
    )
    if len(timestamps) < 2:
        return None

    gaps_hours = [
        (timestamps[i + 1] - timestamps[i]).total_seconds() / 3600
        for i in range(len(timestamps) - 1)
    ]
    return "daily" if median(gaps_hours) <= DAILY_MEDIAN_GAP_HOURS else "sporadic"
