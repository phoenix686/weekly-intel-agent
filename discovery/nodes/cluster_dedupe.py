"""
cluster_dedupe_node: URL-heuristic deduplication of raw bookmark items.

Groups RawItems by normalized URL, selects the representative (longest text,
breaking ties by earliest fetched_at), and returns one ClusteredItem per
unique URL. No LangGraph imports, no LLM calls.
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from urllib.parse import urlparse, urlunparse, parse_qs, urlencode

from state import DiscoverySubgraphState, RawItem, ClusteredItem, NodeCost
from discovery.seen_items import filter_unseen

logger = logging.getLogger(__name__)


_TRACKING_PARAMS = frozenset({
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "utm_id", "utm_source_platform", "s", "ref", "fbclid", "gclid",
    "mc_cid", "mc_eid", "_hsenc", "_hsmi", "hs_email", "hs_automation",
})


def _normalize_url(url: str) -> str:
    """Lowercase, strip trailing slash and fragment, remove tracking query params."""
    if not url:
        return ""
    parsed = urlparse(url.strip().lower())

    if parsed.query:
        params = parse_qs(parsed.query, keep_blank_values=True)
        filtered = {
            k: v for k, v in params.items()
            if k not in _TRACKING_PARAMS and not k.startswith("utm_")
        }
        # Sort keys so param order doesn't create false non-duplicates
        new_query = urlencode(sorted(filtered.items()), doseq=True)
    else:
        new_query = ""

    return urlunparse((
        parsed.scheme,
        parsed.netloc,
        parsed.path.rstrip("/"),
        parsed.params,
        new_query,
        "",  # strip fragment
    ))


def _pick_representative(group: list[RawItem]) -> RawItem:
    """Longest text wins; earliest fetched_at breaks ties."""
    return sorted(group, key=lambda x: (-len(x["text"]), x["fetched_at"]))[0]


def _dedupe(raw_items: list[RawItem]) -> list[ClusteredItem]:
    groups: dict[str, list[RawItem]] = defaultdict(list)
    for item in raw_items:
        groups[_normalize_url(item["url"])].append(item)

    result: list[ClusteredItem] = []
    for group in groups.values():
        rep = _pick_representative(group)
        item = ClusteredItem(
            url=rep["url"],
            title=rep["title"],
            text=rep["text"],
            author_name=rep["author_name"],
            author_handle=rep["author_handle"],
            fetched_at=rep["fetched_at"],
            is_thread=rep["is_thread"],
            thread_contents=rep["thread_contents"],
            expanded_urls=rep["expanded_urls"],
            source=rep["source"],
            duplicate_count=len(group),
        )
        if "has_video" in rep:
            item["has_video"] = rep["has_video"]
        if "video_url" in rep:
            item["video_url"] = rep["video_url"]
        result.append(item)
    return result


def cluster_dedupe_node(state: DiscoverySubgraphState) -> dict:
    t0 = time.perf_counter()
    clustered = _dedupe(state["raw_items"])

    unseen, seen_urls = filter_unseen(clustered)
    if seen_urls:
        logger.info(f"cluster_dedupe: skipped {len(seen_urls)} already-seen item(s): {seen_urls}")

    return {
        "clustered_items": unseen,
        "costs": [NodeCost(
            node_name="cluster_dedupe",
            input_tokens=0,
            output_tokens=0,
            latency_ms=round((time.perf_counter() - t0) * 1000, 4),
            cost_usd=0.0
        )],
    }
