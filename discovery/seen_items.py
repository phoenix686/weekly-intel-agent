"""
Cross-run dedup: tracks item URLs that have already been scored, so a
repeated item (e.g. the same TLDR AI issue or HN thread resurfacing on a
later day) never reaches score_node's paid Haiku call again.

"Seen" = already scored, regardless of keep=True or keep=False.

Rolling 35-day expiry (2026-07-18), same pattern as recent_item_embeddings'
7-day window (discovery/semantic_dedup.py's _WINDOW_DAYS): every source
only ever fetches its 5-15 most recent items (discovery/config/
blog_sources.yaml's fetch_limit), so an entry older than this window is
provably unreachable again -- dead weight, not real cross-run dedup
coverage. 35 days (top half of the 30-45 day range this was scoped to)
gives real buffer for the slowest, sunday-bucket/weekly-cadence sources
(fetch_limit=6): even a multi-week gap in that source's publishing
schedule shouldn't let an old, already-scored item wrongly reappear as
"new" before it's expired here too.

No langgraph imports, no LLM calls.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone

from langgraph.store.base import GetOp, PutOp
from sunday.memory_store_config import get_store

logger = logging.getLogger(__name__)

_NAMESPACE = ("weekly_intel", "seen_items")
_WINDOW_DAYS = 35


def _expire_stale_entries(store) -> int:
    """Lazy sweep: runs once per filter_unseen() call (the one place every
    real run already touches this namespace), deletes any entry whose
    seen_at is older than _WINDOW_DAYS. One store.search() + one batched
    store.batch() of deletes -- store.delete() is itself just
    PutOp(namespace, key, None) under the hood (langgraph.store.base), so
    N deletes batch into one real round trip the same way N writes do,
    not one store.delete() per stale entry.

    An entry with no seen_at at all (pre-2026-07-18 migration) is treated
    as NOT yet eligible for expiry, not as already-expired -- there's no
    real signal for how old those actually are, and deleting real
    cross-run dedup history on a guess would be the same kind of mistake
    already flagged once tonight. scripts/backfill_seen_items_timestamp.py
    backfills those with today's date once, after which every entry ages
    out of this window normally."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=_WINDOW_DAYS)
    all_entries = store.search(_NAMESPACE, limit=10000)
    stale_keys = [
        entry.key for entry in all_entries
        if entry.value.get("seen_at") is not None
        and datetime.fromisoformat(entry.value["seen_at"]) < cutoff
    ]
    if stale_keys:
        store.batch([PutOp(_NAMESPACE, key, None) for key in stale_keys])
    return len(stale_keys)


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

    logger.debug("seen_items: BEFORE _expire_stale_entries()")
    t0 = time.perf_counter()
    expired_count = _expire_stale_entries(store)
    logger.debug(f"seen_items: AFTER _expire_stale_entries() ({time.perf_counter() - t0:.3f}s, {expired_count} expired)")

    logger.debug(f"seen_items: BEFORE store.batch() (GetOp, {len(items)} item(s))")
    t0 = time.perf_counter()
    results = store.batch([GetOp(_NAMESPACE, item["url"]) for item in items])
    logger.debug(f"seen_items: AFTER store.batch() (GetOp) ({time.perf_counter() - t0:.3f}s)")
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
    seen_at = datetime.now(timezone.utc).isoformat()
    logger.debug(f"seen_items: BEFORE store.batch() (PutOp, {len(urls)} url(s))")
    t0 = time.perf_counter()
    store.batch([PutOp(_NAMESPACE, url, {"seen": True, "seen_at": seen_at}) for url in urls])
    logger.debug(f"seen_items: AFTER store.batch() (PutOp) ({time.perf_counter() - t0:.3f}s)")
    logger.info(f"seen_items: marked {len(urls)} url(s) as seen")
