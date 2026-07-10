import time

from discovery.parsers.scrape_blogs import fetch_blog_entries
from state import DiscoverySubgraphState, RawItem, NodeCost


def scrape_blogs(state: DiscoverySubgraphState) -> dict:
    t0 = time.perf_counter()
    result = fetch_blog_entries()

    items: list[RawItem] = []
    for row in result.rows:
        items.append(RawItem(
            source="blog_scrape",
            title=row["title"],
            text=row["text"],
            url=row["url"],
            author_name=row["author_name"],
            author_handle=row["author_handle"],
            fetched_at=row["fetched_at"],
            is_thread=row["is_thread"],
            thread_contents=row["thread_contents"],
            expanded_urls=row["expanded_urls"],
        ))

    cost = NodeCost(
        node_name="scrape_blogs",
        input_tokens=0, output_tokens=0,
        latency_ms=round((time.perf_counter() - t0) * 1000, 4),
        cost_usd=0.0,
    )
    return {
        "raw_items": items,
        "costs": [cost],
        "errors": [f"{feed}: {msg}" for feed, msg in result.errors],
    }
