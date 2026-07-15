"""
Loads discovery/config/blog_sources.yaml -- the merged blog/newsletter
source list (daily + sunday buckets). No langgraph imports, no I/O side
effects beyond reading this one file.
"""

from __future__ import annotations

from pathlib import Path

import yaml

CONFIG_PATH = Path(__file__).parent / "config" / "blog_sources.yaml"


def load_blog_sources() -> list[dict]:
    """Returns every entry: {name, bucket, feed_url|scrape_url}."""
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f) or []


def get_source(name: str) -> dict:
    """Look up a single source entry by its `name`. Raises KeyError if
    not found -- a missing config entry for a node that expects one is a
    real configuration bug, not something to silently no-op around."""
    for entry in load_blog_sources():
        if entry["name"] == name:
            return entry
    raise KeyError(f"No blog_sources.yaml entry named {name!r}")


def feed_urls_for_bucket(bucket: str) -> list[str]:
    """RSS/Atom feed_urls for every entry in the given bucket. Entries with
    scrape_url (HTML fallback, e.g. Anthropic's dev blog) are excluded --
    those need their own dedicated fetch mechanism, not the generic
    RSS-only scrape_blogs path."""
    return [
        entry["feed_url"]
        for entry in load_blog_sources()
        if entry["bucket"] == bucket and "feed_url" in entry
    ]
