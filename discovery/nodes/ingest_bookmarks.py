import os
import time

from discovery.parsers.bookmarks_json import parse_bookmarks_json
from state import DiscoverySubgraphState, RawItem, NodeCost


def ingest_bookmarks(state: DiscoverySubgraphState) -> dict:
    t0 = time.perf_counter()
    path = os.environ.get("TWILLOT_JSON_PATH", "data/tweets.json")
    result = parse_bookmarks_json(path)

    items: list[RawItem] = []
    for row in result.rows:
        items.append(RawItem(
            source="twillot_bootstrap",
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
        node_name="ingest_bookmarks",
        input_tokens=0,
        output_tokens=0,
        latency_ms=round((time.perf_counter() - t0) * 1000, 4),
    )

    return {
        "raw_items": items,
        "costs": [cost],
        "errors": [f"row {i}: {e}" for i, e in result.errors],
    }
