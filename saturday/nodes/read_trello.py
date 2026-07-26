import time
import logging
from core.state import SaturdayGraphState, NodeCost
from saturday.trello_client import fetch_board_cards
from saturday.card_movement import detect_card_movement

logger = logging.getLogger(__name__)

def read_trello(state: SaturdayGraphState) -> dict:
    t0 = time.perf_counter()
    cards = fetch_board_cards()
    logger.info(f"read_trello fetched {len(cards)} cards (run_id={state['run_id']})")

    movements = detect_card_movement(state["run_id"])
    logger.info(f"read_trello detected {len(movements)} card movement(s) since the prior plan (run_id={state['run_id']})")

    cost = NodeCost(
        node_name="read_trello", input_tokens=0, output_tokens=0,
        cost_usd=0.0, latency_ms=round((time.perf_counter() - t0) * 1000, 2),
    )
    return {
        "trello_cards": cards,
        "card_movements": movements,
        "costs": [cost],
    }