import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from discovery.parsers.scrape_blogs import fetch_one_source, fetch_agentmail_sources
from discovery.blog_sources_config import entries_for_context
from core.state import DiscoverySubgraphState, RawItem, NodeCost
from core.observability import record_node_summary
from saturday.memory_store_config import get_store

_SOURCE_HEALTH_NAMESPACE = ("weekly_intel", "source_health")


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
    node_t0 = time.perf_counter()
    items: list[RawItem] = []
    costs: list[NodeCost] = []
    errors: list[str] = []

    entries = entries_for_context(state["source_context"])
    with ThreadPoolExecutor(max_workers=min(8, max(1, len(entries) + 1))) as executor:
        futures = [(entry, time.perf_counter(), executor.submit(fetch_one_source, entry)) for entry in entries]
        agentmail_started = time.perf_counter()
        agentmail_future = executor.submit(fetch_agentmail_sources, state["source_context"])

    health_records = []
    for entry, started, future in futures:
        source_result = future.result()
        cost = NodeCost(
            node_name="scrape_blogs",
            input_tokens=0, output_tokens=0,
            latency_ms=round((time.perf_counter() - started) * 1000, 4),
            cost_usd=0.0,
        )
        if source_result.error is not None:
            cost["error"] = f"{source_result.name}: {source_result.error}"
            errors.append(cost["error"])
        costs.append(cost)
        items.extend(_row_to_item(row) for row in source_result.rows)
        health_records.append((source_result.name, source_result.error, len(source_result.rows)))

    # AgentMail-sourced newsletters (discovery/config/agentmail_sources.yaml,
    # gitignored) aren't blog_sources.yaml entries -- one shared inbox
    # covers up to 10 real senders via a single fetch, split into one
    # SourceResult per real sender for the same per-source NodeCost.error
    # visibility every other source gets.
    agentmail_results = agentmail_future.result()
    agentmail_elapsed_ms = round((time.perf_counter() - agentmail_started) * 1000, 4)
    for source_result in agentmail_results:
        cost = NodeCost(
            node_name="scrape_blogs",
            input_tokens=0, output_tokens=0,
            latency_ms=agentmail_elapsed_ms,
            cost_usd=0.0,
        )
        if source_result.error is not None:
            cost["error"] = f"{source_result.name}: {source_result.error}"
            errors.append(cost["error"])
        costs.append(cost)
        items.extend(_row_to_item(row) for row in source_result.rows)
        health_records.append((source_result.name, source_result.error, len(source_result.rows)))

    try:
        store = get_store()
        now = datetime.now(timezone.utc).isoformat()
        for name, error, row_count in health_records:
            store.put(_SOURCE_HEALTH_NAMESPACE, name, {
                "source": name, "status": "degraded" if error else "healthy",
                "error": str(error)[:300] if error else None,
                "items_fetched": row_count, "checked_at": now, "run_id": state["run_id"],
            })
    except Exception as exc:
        errors.append(f"source_health_persistence_failed: {exc}")

    # items_in/items_out here mean "active sources attempted" / "raw items
    # fetched" -- a different unit pair than cluster_dedupe's (items in,
    # items out of the same kind), but the same generic node_summary shape,
    # documented per-node rather than inventing a new schema per node.
    record_node_summary(
        run_id=state["run_id"],
        node_name="scrape_blogs",
        items_in=len(entries) + len(agentmail_results),
        items_out=len(items),
        duration_seconds=round(time.perf_counter() - node_t0, 3),
        error_summary="; ".join(errors) if errors else None,
    )

    return {
        "raw_items": items,
        "costs": costs,
        "errors": errors,
    }
