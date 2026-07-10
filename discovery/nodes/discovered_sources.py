import time

from discovery.parsers.rss_common import fetch_rss_feed
from discovery.source_config import load_sources
from state import DiscoverySubgraphState, RawItem, NodeCost


def _fetch_bucket(bucket: str) -> dict:
    t0 = time.perf_counter()
    sources = load_sources().get(bucket, [])

    items: list[RawItem] = []
    errors: list[str] = []
    for source in sources:
        result = fetch_rss_feed(source["feed_url"], source_name=source["name"])
        errors.extend(f"{source['name']}: {msg}" for _, msg in result.errors)
        for row in result.rows:
            item = RawItem(
                source=f"discovered:{source['name']}",
                title=row["title"],
                text=row["text"],
                url=row["url"],
                author_name=row["author_name"],
                author_handle=row["author_handle"],
                fetched_at=row["fetched_at"],
                is_thread=row["is_thread"],
                thread_contents=row["thread_contents"],
                expanded_urls=row["expanded_urls"],
            )
            if "has_video" in row:
                item["has_video"] = row["has_video"]
            if "video_url" in row:
                item["video_url"] = row["video_url"]
            items.append(item)

    cost = NodeCost(
        node_name=f"discovered_sources_{bucket}",
        input_tokens=0, output_tokens=0,
        latency_ms=round((time.perf_counter() - t0) * 1000, 4),
        cost_usd=0.0,
    )
    return {"raw_items": items, "costs": [cost], "errors": errors}


def discovered_daily_sources(state: DiscoverySubgraphState) -> dict:
    """Fetches every source in data/sources.json's 'daily' bucket. No-op
    (returns no items) until Part C's approval flow adds entries."""
    return _fetch_bucket("daily")


def discovered_sunday_sources(state: DiscoverySubgraphState) -> dict:
    """Fetches every source in data/sources.json's 'sunday' bucket. No-op
    (returns no items) until Part C's approval flow adds entries."""
    return _fetch_bucket("sunday")
