"""
Cross-run dedup: tracks item URLs that have already been scored, so a
repeated item (e.g. the same TLDR AI issue or HN thread resurfacing on a
later day) never reaches score_node's paid Haiku call again.

"Seen" = already scored, regardless of keep=True or keep=False. No expiry.

No langgraph imports, no LLM calls.
"""

from __future__ import annotations

import logging

from langgraph.store.base import GetOp, PutOp
from sunday.memory_store_config import get_store

logger = logging.getLogger(__name__)

_NAMESPACE = ("weekly_intel", "seen_items")


def filter_unseen(items: list[dict]) -> tuple[list[dict], list[str]]:
    """Split items into (unseen, seen_urls) by checking each item's `url`
    against the seen_items store. Unseen items are returned in their
    original order; seen_urls lists the urls that were dropped.

    One batched store.batch() call covering every item, not one
    store.get() per item -- measured 12.1x faster on a real 10-key
    benchmark against the live store (2636ms looped vs 219ms batched),
    the most concrete inefficiency found in the real 45-minute Sunday
    timeout investigation. Real Sunday-run volume (up to ~108 raw items)
    made this loop itself a material cost, independent of the model-load
    tax and per-item embedding write loops fixed separately."""
    if not items:
        return [], []
    store = get_store()
    results = store.batch([GetOp(_NAMESPACE, item["url"]) for item in items])
    unseen: list[dict] = []
    seen_urls: list[str] = []
    for item, result in zip(items, results):
        if result is not None:
            seen_urls.append(item["url"])
        else:
            unseen.append(item)
    return unseen, seen_urls


def mark_seen(urls: list[str]) -> None:
    """Record each url as seen. Call once scoring has actually completed
    for a run -- not before, so a crash mid-run never marks an item seen
    without it having really been scored.

    One batched store.batch() call covering every url, not one
    store.put() per url -- same fix and rationale as filter_unseen."""
    if not urls:
        return
    store = get_store()
    store.batch([PutOp(_NAMESPACE, url, {"seen": True}) for url in urls])
    logger.info(f"seen_items: marked {len(urls)} url(s) as seen")
