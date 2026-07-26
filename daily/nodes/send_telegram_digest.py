import time
from telegram.bot_client import send_message
from saturday.memory_store_config import get_store
from core.state import DailyGraphState, NodeCost


def send_telegram_digest(state: DailyGraphState) -> dict:
    t0 = time.monotonic()
    response = send_message(state["digest_text"])

    item_map = state.get("digest_item_map")
    if item_map:
        message_id = response["result"]["message_id"]
        get_store().put(
            ("weekly_intel", "digest_item_map"),
            str(message_id),
            {"run_id": state["run_id"], "items": item_map},
        )

    cost = NodeCost(
        node_name="send_telegram_digest",
        input_tokens=0,
        output_tokens=0,
        cost_usd=0.0,
        latency_ms=round((time.monotonic() - t0) * 1000, 2),
    )
    return {"costs": [cost]}
