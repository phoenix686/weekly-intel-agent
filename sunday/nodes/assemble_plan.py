import time
import logging

from state import SundayGraphState, NodeCost

logger = logging.getLogger(__name__)

def assemble_plan(state: SundayGraphState) -> dict:
    t0 = time.perf_counter()
    text, item_map = format_plan(
        state["classified_items"],
        len(state["pending_approvals"]),
        state["run_id"],
        state["trello_cards"],
    )
    cost = NodeCost(
        node_name="assemble_plan", input_tokens=0, output_tokens=0,
        cost_usd=0.0, latency_ms=round((time.perf_counter() - t0) * 1000, 2),
    )
    return {"plan_text": text, "plan_item_map": item_map, "costs": [cost]}
def format_plan(
    classified_items: list[dict],
    pending_approvals_count: int,
    run_id: str,
    trello_cards: list[dict],
) -> tuple[str, dict[int, dict]]:
    card_by_id = {c["card_id"]: c for c in trello_cards}
    plan_items = [i for i in classified_items if i["classification"] == "plan_item"]

    if not plan_items:
        msg = "📋 *Weekly Plan*\n\n_Nothing on the plan this week."
        if pending_approvals_count > 0:
            msg += f" {pending_approvals_count} proposals pending approval — check Telegram."
        msg += "_"
        return msg, {}

    reading = [i for i in plan_items if i.get("matched_card_id") is None]
    project = [i for i in plan_items if i.get("matched_card_id") is not None]

    lines = ["📋 *Weekly Plan*", ""]
    counter = 1
    item_map: dict[int, dict] = {}

    if reading:
        lines.append("**Reading & Learning**")
        for item in reading:
            title = (item.get("title") or item["text"])[:80]
            reasoning = item["reasoning"].replace("_", r"\_")
            lines.append(f"{counter}. [{title}]({item['url']})")
            lines.append(f"   _{reasoning}_")
            lines.append("")
            item_map[counter] = {
                "url": item["url"], "title": title,
                "text": item["text"], "tags": item.get("tags", []),
                "reasoning": item["reasoning"],
            }
            counter += 1

    if project:
        lines.append("**Existing Project Work**")
        for item in project:
            title = (item.get("title") or item["text"])[:80]
            reasoning = item["reasoning"].replace("_", r"\_")
            card = card_by_id.get(item["matched_card_id"], {})
            card_name = card.get("name", item["matched_card_id"])
            lines.append(f"{counter}. [{title}]({item['url']})")
            lines.append(f'   _{reasoning}_ — continues card: "{card_name}"')
            lines.append("")
            item_map[counter] = {
                "url": item["url"], "title": title,
                "text": item["text"], "tags": item.get("tags", []),
                "reasoning": item["reasoning"],
            }
            counter += 1

    if pending_approvals_count > 0:
        footer = f"_{len(plan_items)} plan items · {pending_approvals_count} proposals pending approval · run: {run_id[:8]}_"
    else:
        footer = f"_{len(plan_items)} plan items · run: {run_id[:8]}_"
    lines.append(footer)

    return "\n".join(lines), item_map


