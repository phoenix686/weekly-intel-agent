import json
import logging
import os
import urllib.parse
import urllib.request

logger = logging.getLogger(__name__)

BRAIN_BOARD_ID = "<REDACTED_TRELLO_BOARD_ID>"
TRELLO_API_BASE = "https://api.trello.com/1"
RELEVANT_LIST_NAMES = {"Dump", "In Progress"}


def _trello_get(path: str, params: dict | None = None) -> dict | list:
    api_key = os.environ.get("TRELLO_API_KEY")
    token = os.environ.get("TRELLO_TOKEN")
    if not api_key:
        raise KeyError("TRELLO_API_KEY is not set in the environment")
    if not token:
        raise KeyError("TRELLO_TOKEN is not set in the environment")

    query = {"key": api_key, "token": token}
    if params:
        query.update(params)

    url = f"{TRELLO_API_BASE}{path}?{urllib.parse.urlencode(query)}"
    with urllib.request.urlopen(url) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_board_cards(board_id: str = BRAIN_BOARD_ID) -> list[dict]:
    """Fetch all open cards from all open lists on the Brain board.

    Returns plain dicts: card_id, name, desc, list_id, list_name, url.
    Including list_name lets correlate_trello distinguish the Dump inbox
    from active project lists without needing hardcoded list IDs.
    """
    all_lists = _trello_get(f"/boards/{board_id}/lists", {"filter": "open"})
    relevant = {lst["id"]: lst["name"] for lst in all_lists if lst["name"] in RELEVANT_LIST_NAMES}
    logger.info(f"fetch_board_cards: matched lists {list(relevant.values())} on board {board_id}")

    result = []
    for list_id, list_name in relevant.items():
        cards = _trello_get(f"/lists/{list_id}/cards", {"filter": "open", "checklists": "all"})
        for card in cards:
            checklist_items = [
                item["name"]
                for checklist in card.get("checklists", [])
                for item in checklist.get("checkItems", [])
            ]
            result.append({
                "card_id": card["id"],
                "name": card["name"],
                "desc": card.get("desc", ""),
                "list_id": list_id,
                "list_name": list_name,
                "url": card.get("shortUrl", ""),
                "checklist_items": checklist_items,
            })

    logger.info(f"fetch_board_cards: fetched {len(result)} cards")
    return result
