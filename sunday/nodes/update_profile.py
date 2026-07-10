import csv
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

from state import SundayGraphState, NodeCost

logger = logging.getLogger(__name__)


def update_profile(state: SundayGraphState) -> dict:
    t0 = time.perf_counter()

    total_cost = sum(c["cost_usd"] for c in state["costs"])
    plan_items = sum(1 for i in state["classified_items"] if i.get("classification") == "plan_item")
    proposals = len(state["pending_approvals"])

    cost_log = Path("data/cost_log.csv")
    write_header = not cost_log.exists()
    with cost_log.open("a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if write_header:
            writer.writerow(["run_id", "timestamp", "total_cost_usd", "plan_items", "proposals"])
        writer.writerow([
            state["run_id"],
            datetime.now(timezone.utc).isoformat(),
            round(total_cost, 6),
            plan_items,
            proposals,
        ])

    logger.info(
        f"update_profile: run {state['run_id']} — "
        f"{plan_items} plan_items, {proposals} proposals pending, "
        f"${total_cost:.6f}"
    )

    cost = NodeCost(
        node_name="update_profile",
        input_tokens=0, output_tokens=0, cost_usd=0.0,
        latency_ms=round((time.perf_counter() - t0) * 1000, 2),
    )
    return {"costs": [cost]}
