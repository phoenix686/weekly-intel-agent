import time
from telegram.bot_client import send_message
from sunday.memory_store_config import get_store
from core.state import SundayGraphState, NodeCost


def send_telegram_plan(state: SundayGraphState) -> dict:
    t0 = time.monotonic()
    response = send_message(state["plan_text"])

    item_map = state.get("plan_item_map")
    if item_map:
        message_id = response["result"]["message_id"]
        get_store().put(
            ("weekly_intel", "digest_item_map"),
            str(message_id),
            {"run_id": state["run_id"], "items": item_map},
        )

    cost = NodeCost(
        node_name="send_telegram_plan",
        input_tokens=0,
        output_tokens=0,
        cost_usd=0.0,
        latency_ms=round((time.monotonic() - t0) * 1000, 2),
    )
    return {"costs": [cost]}
