"""
Cross-run dedup: tracks item URLs that have already been scored, so a
repeated item (e.g. the same TLDR AI issue or HN thread resurfacing on a
later day) never reaches score_node's paid Haiku call again.

"Seen" = already scored, regardless of keep=True or keep=False. No expiry.

No langgraph imports, no LLM calls.
"""

from __future__ import annotations

import logging

from sunday.memory_store_config import get_store

logger = logging.getLogger(__name__)

_NAMESPACE = ("weekly_intel", "seen_items")


def filter_unseen(items: list[dict]) -> tuple[list[dict], list[str]]:
    """Split items into (unseen, seen_urls) by checking each item's `url`
    against the seen_items store. Unseen items are returned in their
    original order; seen_urls lists the urls that were dropped."""
    store = get_store()
    unseen: list[dict] = []
    seen_urls: list[str] = []
    for item in items:
        url = item["url"]
        if store.get(_NAMESPACE, url) is not None:
            seen_urls.append(url)
        else:
            unseen.append(item)
    return unseen, seen_urls


def mark_seen(urls: list[str]) -> None:
    """Record each url as seen. Call once scoring has actually completed
    for a run -- not before, so a crash mid-run never marks an item seen
    without it having really been scored."""
    if not urls:
        return
    store = get_store()
    for url in urls:
        store.put(_NAMESPACE, url, {"seen": True})
    logger.info(f"seen_items: marked {len(urls)} url(s) as seen")
