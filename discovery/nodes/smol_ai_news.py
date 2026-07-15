import time

from discovery.parsers.rss_common import fetch_rss_feed
from discovery.blog_sources_config import get_source
from state import DiscoverySubgraphState, RawItem, NodeCost


def smol_ai_news(state: DiscoverySubgraphState) -> dict:
    t0 = time.perf_counter()
    feed_url = get_source("Smol AI News")["feed_url"]
    result = fetch_rss_feed(feed_url, source_name="smol_ai_news", max_age_hours=48)

    items: list[RawItem] = []
    for row in result.rows:
        item = RawItem(
            source="smol_ai_news",
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
        node_name="smol_ai_news",
        input_tokens=0, output_tokens=0,
        latency_ms=round((time.perf_counter() - t0) * 1000, 4),
        cost_usd=0.0,
    )
    return {
        "raw_items": items,
        "costs": [cost],
        "errors": [f"{source}: {msg}" for source, msg in result.errors],
    }
