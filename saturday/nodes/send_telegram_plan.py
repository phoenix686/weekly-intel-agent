import time
from telegram.bot_client import send_message
from saturday.memory_store_config import get_store
from saturday.plan_history import record_plan_history
from discovery.seen_items import mark_seen
from discovery.semantic_dedup import commit_delivered_embeddings
from telegram.delivery import deliver_telegram
from core.state import SaturdayGraphState, NodeCost


def send_telegram_plan(state: SaturdayGraphState) -> dict:
    t0 = time.monotonic()
    store = get_store()
    item_map = state.get("plan_item_map", {})
    receipt = deliver_telegram(
        pipeline="saturday",
        run_id=state["run_id"],
        text=state["plan_text"],
        item_map=item_map,
        store=store,
        send=send_message,
        mark_seen=mark_seen,
        dry_run=state.get("dry_run", False),
    )
    if receipt.reused:
        return {"costs": [NodeCost(node_name="send_telegram_plan", input_tokens=0,
                                   output_tokens=0, cost_usd=0.0, latency_ms=0.0)]}
    if not state.get("dry_run", False):
        commit_delivered_embeddings(state["run_id"], list(receipt.delivered_urls))

    generated_at = state["plan_generated_at"]
    store.put(
        ("companion",),
        "current_weekly_plan",
        {
            "run_id": state["run_id"],
            "plan_text": state["plan_text"],
            "generated_at": generated_at,
            "message_ids": list(receipt.message_ids),
        },
    )
    village_summary = f"{len(item_map)} plan item(s)" if item_map else "no new content"
    store.put(
        ("village",),
        f"event:{generated_at}",
        {
            "agent": "weekly-intel",
            "event_type": "plan_ready",
            "summary": village_summary,
            "timestamp": generated_at,
        },
    )
    record_plan_history(state["run_id"], state.get("surfaced_cards", []))

    if not state.get("dry_run", False):
        for key in state.get("adhoc_queue_keys", []):
            store.delete(("weekly_intel", "adhoc_queue"), key)

    cost = NodeCost(
        node_name="send_telegram_plan",
        input_tokens=0,
        output_tokens=0,
        cost_usd=0.0,
        latency_ms=round((time.monotonic() - t0) * 1000, 2),
    )
    return {"costs": [cost]}
