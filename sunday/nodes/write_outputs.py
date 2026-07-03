import time
import logging

from state import SundayGraphState, NodeCost
from sunday.trello_client import create_trello_card, update_trello_card, get_dump_list_id
from telegram.bot_client import send_message

logger = logging.getLogger(__name__)


def write_outputs(state: SundayGraphState) -> dict:
    t0 = time.perf_counter()

    for result in state["approval_results"]:
        url = result["item_id"]  # holds the url value, see await_approval fix below
        decision = result["decision"]

        item = next((i for i in state["classified_items"] if i["url"] == url), None)
        if item is None:
            logger.warning(f"write_outputs: no matching classified_item for {url}, skipping")
            continue

        if decision != "approve":
            logger.info(f"write_outputs: {url} rejected, no Trello write")
            continue

        if item.get("proposal_type") == "extend" and item.get("matched_card_id"):
            card = update_trello_card(item["matched_card_id"], desc=item["reasoning"])
            send_message(f"✅ Updated card: {card['name']}\n{card['url']}")
        else:
            title = item.get("title") or item["text"][:80]
            card = create_trello_card(title, get_dump_list_id(), item["reasoning"])
            send_message(f"✅ Created new card: {card['name']}\n{card['url']}")

    cost = NodeCost(
        node_name="write_outputs", input_tokens=0, output_tokens=0,
        cost_usd=0.0, latency_ms=round((time.perf_counter() - t0) * 1000, 2),
    )
    return {"costs": [cost]}