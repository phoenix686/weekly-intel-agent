import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request

logger = logging.getLogger(__name__)

BRAIN_BOARD_ID = "<REDACTED_TRELLO_BOARD_ID>"
TRELLO_API_BASE = "https://api.trello.com/1"
RELEVANT_LIST_NAMES = {"Dump", "In Progress"}
DUMP_LIST_NAME = "Dump"
DONE_LIST_NAME = "Done"  # live-confirmed 2026-07-18 against the real board


def _auth_params() -> dict:
    api_key = os.environ.get("TRELLO_API_KEY")
    token = os.environ.get("TRELLO_TOKEN")
    if not api_key:
        raise KeyError("TRELLO_API_KEY is not set in the environment")
    if not token:
        raise KeyError("TRELLO_TOKEN is not set in the environment")
    return {"key": api_key, "token": token}


def _trello_get(path: str, params: dict | None = None) -> dict | list:
    query = _auth_params()
    if params:
        query.update(params)
    url = f"{TRELLO_API_BASE}{path}?{urllib.parse.urlencode(query)}"
    with urllib.request.urlopen(url) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _trello_post(path: str, params: dict | None = None) -> dict | list:
    query = _auth_params()
    if params:
        query.update(params)
    url = f"{TRELLO_API_BASE}{path}?{urllib.parse.urlencode(query)}"
    req = urllib.request.Request(url, method="POST")
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _trello_put(path: str, params: dict | None = None) -> dict | list:
    query = _auth_params()
    if params:
        query.update(params)
    url = f"{TRELLO_API_BASE}{path}?{urllib.parse.urlencode(query)}"
    req = urllib.request.Request(url, method="PUT")
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_board_cards(board_id: str = BRAIN_BOARD_ID) -> list[dict]:
    """Fetch all open cards from all open lists on the Brain board.

    Returns plain dicts: card_id, name, desc, list_id, list_name, url,
    checklist_items, last_activity. Including list_name lets correlate_trello
    distinguish the Dump inbox from active project lists without needing
    hardcoded list IDs. last_activity is Trello's own dateLastActivity
    (ISO 8601 string, e.g. "2026-05-31T12:17:18.243Z") -- already present in
    Trello's default card response, no extra API param needed; live-verified
    2026-07-18 against the real board. Exposed for downstream staleness/
    cross-week movement use (Sunday plan LLM prioritization checkpoint,
    sub-phase 2) -- not read by correlate_trello today.
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
                "last_activity": card.get("dateLastActivity"),
            })

    logger.info(f"fetch_board_cards: fetched {len(result)} cards")
    return result


def fetch_list_id_to_name_map(board_id: str = BRAIN_BOARD_ID) -> dict[str, str]:
    """Return {list_id: list_name} for EVERY open list on the board, not just
    RELEVANT_LIST_NAMES -- includes "Done" and any other list a card might
    have moved to since it was last surfaced. Used by cross-week movement
    detection (Sunday plan LLM prioritization checkpoint, sub-phase 4) to
    resolve a card's current idList to a human-readable name; fetch_board_cards()
    itself deliberately still only fetches Dump/In Progress cards -- Done-list
    cards should never enter correlate_trello's matching pool."""
    all_lists = _trello_get(f"/boards/{board_id}/lists", {"filter": "open"})
    return {lst["id"]: lst["name"] for lst in all_lists}


def fetch_card_current_state(card_id: str) -> dict | None:
    """Real current state of one card by ID, regardless of which list it's
    in now (including Done) or whether it's been archived -- ground truth
    for cross-week movement detection (sub-phase 4), not a re-fetch of an
    entire list. Returns None only if the card was permanently deleted
    (real Trello 404, distinct from closed=True/"archived", which Trello
    represents as a live card with closed=True, not a 404). Any other HTTP
    error propagates -- a real API failure should not be silently read as
    "card deleted"."""
    try:
        card = _trello_get(f"/cards/{card_id}", {"fields": "name,idList,closed"})
    except urllib.error.HTTPError as e:
        if e.code == 404:
            logger.info(f"fetch_card_current_state: card {card_id} not found (404) -- treated as permanently deleted")
            return None
        raise
    return {"card_id": card["id"], "name": card["name"], "list_id": card["idList"], "closed": card["closed"]}


def get_dump_list_id(board_id: str = BRAIN_BOARD_ID) -> str:
    """Return the list ID for the Dump list. Used when creating new proposal cards."""
    all_lists = _trello_get(f"/boards/{board_id}/lists", {"filter": "open"})
    for lst in all_lists:
        if lst["name"] == DUMP_LIST_NAME:
            return lst["id"]
    raise ValueError(f"No '{DUMP_LIST_NAME}' list found on board {board_id}")


def create_trello_card(name: str, list_id: str, desc: str = "") -> dict:
    """Create a new card in the given list. Returns plain dict: card_id, name, url."""
    params = {"idList": list_id, "name": name}
    if desc:
        params["desc"] = desc
    card = _trello_post("/cards", params)
    logger.info(f"create_trello_card: created '{name}' in list {list_id} (card_id={card['id']})")
    return {"card_id": card["id"], "name": card["name"], "url": card.get("shortUrl", "")}


def update_trello_card(card_id: str, name: str | None = None, desc: str | None = None) -> dict:
    """Update a card's name and/or desc. Returns plain dict: card_id, name, url."""
    params = {}
    if name is not None:
        params["name"] = name
    if desc is not None:
        params["desc"] = desc
    if not params:
        raise ValueError("update_trello_card: at least one of name or desc must be provided")
    card = _trello_put(f"/cards/{card_id}", params)
    logger.info(f"update_trello_card: updated card {card_id}")
    return {"card_id": card["id"], "name": card["name"], "url": card.get("shortUrl", "")}
