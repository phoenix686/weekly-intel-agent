import time
from telegram.bot_client import send_message
from saturday.memory_store_config import get_store
from saturday.plan_history import record_plan_history
from saturday.carry_forward import commit_carried
from discovery.seen_items import mark_seen
from discovery.semantic_dedup import commit_delivered_embeddings
from telegram.delivery import deliver_telegram, mark_domain_commit_complete
from core.state import SaturdayGraphState, NodeCost


def send_telegram_plan(state: SaturdayGraphState) -> dict:
    t0 = time.monotonic()
    store = get_store()
    item_map = state.get("plan_item_map", {})
    domain_payload = {
        "run_id": state["run_id"], "text": state["plan_text"], "item_map": item_map,
        "generated_at": state["plan_generated_at"],
        "surfaced_cards": state.get("surfaced_cards", []),
        "carry_forward_urls": state.get("carry_forward_urls", []),
        "adhoc_queue_keys": state.get("adhoc_queue_keys", []),
        "dry_run": state.get("dry_run", False),
    }
    receipt = deliver_telegram(
        pipeline="saturday",
        run_id=state["run_id"],
        text=state["plan_text"],
        item_map=item_map,
        store=store,
        send=send_message,
        mark_seen=mark_seen,
        dry_run=state.get("dry_run", False),
        domain_commit_payload=domain_payload,
    )
    payload = receipt.domain_commit_payload
    if not payload.get("dry_run", False):
        commit_delivered_embeddings(receipt.run_id, list(receipt.delivered_urls))

    generated_at = payload["generated_at"]
    store.put(
        ("companion",),
        "current_weekly_plan",
        {
            "run_id": receipt.run_id,
            "plan_text": payload["text"],
            "generated_at": generated_at,
            "message_ids": list(receipt.message_ids),
        },
    )
    village_summary = (
        f"{len(payload['item_map'])} plan item(s)"
        if payload["item_map"] else "no new content"
    )
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
    record_plan_history(receipt.run_id, payload.get("surfaced_cards", []))
    commit_carried(payload.get("carry_forward_urls", []), receipt.run_id)

    if not payload.get("dry_run", False):
        for key in payload.get("adhoc_queue_keys", []):
            store.delete(("weekly_intel", "adhoc_queue"), key)
    mark_domain_commit_complete(store, receipt)
    if receipt.reused and receipt.run_id != state["run_id"]:
        return {"costs": [NodeCost(node_name="send_telegram_plan", input_tokens=0,
                                   output_tokens=0, cost_usd=0.0, latency_ms=0.0)]}

    cost = NodeCost(
        node_name="send_telegram_plan",
        input_tokens=0,
        output_tokens=0,
        cost_usd=0.0,
        latency_ms=round((time.monotonic() - t0) * 1000, 2),
    )
    return {"costs": [cost]}
