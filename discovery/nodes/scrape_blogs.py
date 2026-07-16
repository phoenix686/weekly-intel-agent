import time

from discovery.parsers.scrape_blogs import fetch_one_source
from discovery.blog_sources_config import entries_for_context
from state import DiscoverySubgraphState, RawItem, NodeCost


def _row_to_item(row: dict) -> RawItem:
    item = RawItem(
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
    )
    if "has_video" in row:
        item["has_video"] = row["has_video"]
    if "video_url" in row:
        item["video_url"] = row["video_url"]
    return item


def scrape_blogs(state: DiscoverySubgraphState) -> dict:
    """One NodeCost per source (blog_sources.yaml entry), not one per node
    invocation -- each source is fetched and timed individually so its
    NodeCost.latency_ms reflects that source's own real fetch, and
    NodeCost.error (state-nodecost-error-field, Checkpoint 1) carries that
    source's failure message when it fails. A single failing source's
    exception is already caught inside fetch_one_source -- it can never
    crash this loop or block another source's fetch/cost record."""
    items: list[RawItem] = []
    costs: list[NodeCost] = []
    errors: list[str] = []

    for entry in entries_for_context(state["source_context"]):
        t0 = time.perf_counter()
        source_result = fetch_one_source(entry)
        cost = NodeCost(
            node_name="scrape_blogs",
            input_tokens=0, output_tokens=0,
            latency_ms=round((time.perf_counter() - t0) * 1000, 4),
            cost_usd=0.0,
        )
        if source_result.error is not None:
            cost["error"] = f"{source_result.name}: {source_result.error}"
            errors.append(cost["error"])
        costs.append(cost)
        items.extend(_row_to_item(row) for row in source_result.rows)

    return {
        "raw_items": items,
        "costs": costs,
        "errors": errors,
        "stage": "sourced",
    }
