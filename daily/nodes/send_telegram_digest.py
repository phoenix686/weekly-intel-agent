import time
from telegram.bot_client import send_message
from telegram.delivery import deliver_telegram, mark_domain_commit_complete
from saturday.memory_store_config import get_store
from core.state import DailyGraphState, NodeCost
from discovery.seen_items import mark_seen
from discovery.semantic_dedup import commit_delivered_embeddings


def send_telegram_digest(state: DailyGraphState) -> dict:
    t0 = time.monotonic()
    store = get_store()
    item_map = state.get("digest_item_map") or {}
    domain_payload = {
        "run_id": state["run_id"], "text": state["digest_text"],
        "item_map": item_map, "generated_at": state["digest_generated_at"],
    }
    receipt = deliver_telegram(
        pipeline="daily",
        run_id=state["run_id"],
        text=state["digest_text"],
        item_map=item_map,
        store=store,
        send=send_message,
        mark_seen=mark_seen,
        domain_commit_payload=domain_payload,
    )
    payload = receipt.domain_commit_payload
    commit_delivered_embeddings(receipt.run_id, list(receipt.delivered_urls))

    generated_at = payload["generated_at"]
    store.put(
        ("companion",),
        "current_daily_digest",
        {
            "run_id": receipt.run_id,
            "digest_text": payload["text"],
            "digest_item_map": payload["item_map"],
            "generated_at": generated_at,
            "delivery_message_ids": list(receipt.message_ids),
        },
    )
    summary = (
        f"{len(payload['item_map'])} story item(s) delivered"
        if payload["item_map"] else "no new content"
    )
    store.put(
        ("village",),
        f"event:{generated_at}",
        {
            "agent": "weekly-intel",
            "event_type": "digest_ready",
            "summary": summary,
            "timestamp": generated_at,
        },
    )
    mark_domain_commit_complete(store, receipt)
    if receipt.reused and receipt.run_id != state["run_id"]:
        return {"costs": [NodeCost(node_name="send_telegram_digest", input_tokens=0,
                                   output_tokens=0, cost_usd=0.0, latency_ms=0.0)]}

    cost = NodeCost(
        node_name="send_telegram_digest",
        input_tokens=0,
        output_tokens=0,
        cost_usd=0.0,
        latency_ms=round((time.monotonic() - t0) * 1000, 2),
    )
    return {"costs": [cost]}
