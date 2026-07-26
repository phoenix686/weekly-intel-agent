"""
Loads discovery/config/blog_sources.yaml -- the single merged source list
(daily + saturday buckets) that scrape_blogs reads from. No langgraph
imports, no I/O side effects beyond reading this one file.
"""

from __future__ import annotations

from pathlib import Path

import yaml

CONFIG_PATH = Path(__file__).parent / "config" / "blog_sources.yaml"

# bucket=daily entries fire on both daily and saturday invocations (Saturday
# is a superset -- it catches everything the daily digest would, plus
# saturday-only sources). bucket=saturday entries fire on saturday only.
_ACTIVE_BUCKETS = {
    "daily": ("daily",),
    "saturday": ("daily", "saturday"),
}


def load_blog_sources() -> list[dict]:
    """Returns every entry: {name, bucket, feed_url|scrape_url}."""
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f) or []


def entries_for_context(source_context: str) -> list[dict]:
    """Every entry active for this invocation context ("daily" or
    "saturday"), per _ACTIVE_BUCKETS above."""
    if source_context not in _ACTIVE_BUCKETS:
        raise ValueError(f"Unknown source_context: {source_context!r} (expected 'daily' or 'saturday')")
    active = _ACTIVE_BUCKETS[source_context]
    return [entry for entry in load_blog_sources() if entry["bucket"] in active]
