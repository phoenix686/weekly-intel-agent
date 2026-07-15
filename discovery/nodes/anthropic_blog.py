import time

from discovery.parsers.anthropic_blog import fetch_anthropic_engineering
from discovery.blog_sources_config import get_source
from state import DiscoverySubgraphState, RawItem, NodeCost


def anthropic_blog(state: DiscoverySubgraphState) -> dict:
    t0 = time.perf_counter()
    scrape_url = get_source("Anthropic Engineering Blog")["scrape_url"]
    result = fetch_anthropic_engineering(url=scrape_url)

    items: list[RawItem] = []
    for row in result.rows:
        item = RawItem(
            source="anthropic_blog",
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
        node_name="anthropic_blog",
        input_tokens=0, output_tokens=0,
        latency_ms=round((time.perf_counter() - t0) * 1000, 4),
        cost_usd=0.0,
    )
    return {
        "raw_items": items,
        "costs": [cost],
        "errors": [f"{source}: {msg}" for source, msg in result.errors],
    }
