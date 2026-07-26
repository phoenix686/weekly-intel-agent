"""
Cross-week Trello card movement detection (Saturday plan LLM prioritization
checkpoint, sub-phase 4). Compares the most recent prior plan_history
entry's cards against their REAL current Trello state -- ground truth
from Trello's actual API, not a self-reported flag -- to tell whether
each card has been archived, moved to the Done-equivalent list, moved to
some other list, or is unchanged since it was last surfaced.

No LLM call here: movement classification is a deterministic comparison
against real API state (the closed flag, current list name vs. the
recorded list name). The LLM node that consumes this signal is
sub-phase 5, not this one.

No langgraph imports.
"""

from __future__ import annotations

import logging

from saturday.plan_history import get_most_recent_prior_entry
from saturday.trello_client import DONE_LIST_NAME, fetch_card_current_state, fetch_list_id_to_name_map

logger = logging.getLogger(__name__)


def detect_card_movement(run_id: str) -> list[dict]:
    """Real movement, per card, since the most recent prior Saturday run's
    plan. Returns [] if there's no prior plan_history entry to compare
    against (e.g. the first-ever Saturday run) -- permissive, nothing to
    compare yet is not an error.

    Each result: {"card_id", "previous_list_name", "current_list_name",
    "status"} where status is one of:
      - "archived": card's closed flag is True on Trello right now
      - "not_found": card was permanently deleted (real 404), rare
      - "completed": card's current list is the Done-equivalent list
      - "moved": card is in a different (non-Done) list than last surfaced
      - "unchanged": card is still in the same list as last surfaced
    """
    prior_entry = get_most_recent_prior_entry(current_run_id=run_id)
    if prior_entry is None:
        logger.info(f"card_movement: no prior plan_history entry to compare against (run={run_id})")
        return []

    list_id_to_name = fetch_list_id_to_name_map()

    movements = []
    for card in prior_entry["cards"]:
        card_id = card["card_id"]
        previous_list_name = card["list_name"]
        current = fetch_card_current_state(card_id)

        if current is None:
            movements.append({
                "card_id": card_id, "previous_list_name": previous_list_name,
                "current_list_name": None, "status": "not_found",
            })
            continue

        if current["closed"]:
            movements.append({
                "card_id": card_id, "previous_list_name": previous_list_name,
                "current_list_name": list_id_to_name.get(current["list_id"]), "status": "archived",
            })
            continue

        current_list_name = list_id_to_name.get(current["list_id"], "Unknown")
        if current_list_name == DONE_LIST_NAME:
            status = "completed"
        elif current_list_name != previous_list_name:
            status = "moved"
        else:
            status = "unchanged"

        movements.append({
            "card_id": card_id, "previous_list_name": previous_list_name,
            "current_list_name": current_list_name, "status": status,
        })

    logger.info(
        f"card_movement: compared {len(movements)} card(s) against run "
        f"{prior_entry['run_id']} (run={run_id})"
    )
    return movements
