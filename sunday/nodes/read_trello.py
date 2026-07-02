import time
import logging
from state import SundayGraphState, NodeCost
from sunday.trello_client import fetch_board_cards

logger = logging.getLogger(__name__)

def read_trello(state: SundayGraphState) -> dict:
    t0 = time.perf_counter()
    cards = fetch_board_cards()
    logger.info(f"read_trello fetched {len(cards)} cards (run_id={state['run_id']})")
    cost = NodeCost(
        node_name="read_trello", input_tokens=0, output_tokens=0,
        cost_usd=0.0, latency_ms=round((time.perf_counter() - t0) * 1000, 2),
    )
    return {
        "trello_cards": cards,
        "costs": [cost],
    }