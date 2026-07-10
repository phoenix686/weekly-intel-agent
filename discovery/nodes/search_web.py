import time

from discovery.parsers.search_web import run_searches
from state import DiscoverySubgraphState, RawItem, NodeCost

# TODO: confirm how queries are sourced — hardcoded list, derived from taste profile, or passed via state
SEARCH_QUERIES: list[str] = []


def search_web(state: DiscoverySubgraphState) -> dict:
    t0 = time.perf_counter()
    result = run_searches(SEARCH_QUERIES)

    items: list[RawItem] = []
    for row in result.rows:
        items.append(RawItem(
            source="web_search",
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
        node_name="search_web",
        input_tokens=0, output_tokens=0,
        latency_ms=round((time.perf_counter() - t0) * 1000, 4),
        cost_usd=0.0,
    )
    return {
        "raw_items": items,
        "costs": [cost],
        "errors": [f"{query}: {msg}" for query, msg in result.errors],
    }
