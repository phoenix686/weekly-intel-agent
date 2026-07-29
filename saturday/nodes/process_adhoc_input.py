import time
from datetime import datetime, timezone

from core.state import DiscoverySubgraphState, RawItem, NodeCost
from saturday.memory_store_config import get_store


def process_adhoc_input(state: DiscoverySubgraphState) -> dict:
    t0 = time.perf_counter()
    store = get_store()

    queued = store.search(("weekly_intel", "adhoc_queue"), limit=200)

    items: list[RawItem] = []
    queue_keys: list[str] = []

    for item_obj in queued:
        data = item_obj.value
        text = data.get("text", "").strip()
        if not text:
            continue
        items.append(RawItem(
            source="adhoc_telegram",
            url=f"adhoc:{item_obj.key}",
            title=text[:80],
            text=text,
            author_name="",
            author_handle="",
            fetched_at=data.get("queued_at", datetime.now(timezone.utc).isoformat()),
            is_thread=False,
            thread_contents=None,
            expanded_urls=[],
        ))
        queue_keys.append(item_obj.key)

    cost = NodeCost(
        node_name="process_adhoc_input",
        input_tokens=0, output_tokens=0, cost_usd=0.0,
        latency_ms=round((time.perf_counter() - t0) * 1000, 2),
    )
    return {"raw_items": items, "adhoc_queue_keys": queue_keys, "costs": [cost]}
