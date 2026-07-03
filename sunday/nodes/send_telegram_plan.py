import time
from telegram.bot_client import send_message
from state import SundayGraphState, NodeCost


def send_telegram_plan(state: SundayGraphState) -> dict:
    t0 = time.monotonic()
    send_message(state["plan_text"])
    cost = NodeCost(
        node_name="send_telegram_plan",
        input_tokens=0,
        output_tokens=0,
        cost_usd=0.0,
        latency_ms=round((time.monotonic() - t0) * 1000, 2),
    )
    return {"costs": [cost]}
