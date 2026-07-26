"""
saturday/trello_client.py -- zero unit test coverage existed before the
sub-phase 2 version of this file. Covers the last_activity field
(sub-phase 2, staleness), fetch_list_id_to_name_map and
fetch_card_current_state (sub-phase 4, cross-week movement detection),
plus the pre-existing list-filtering and checklist-flattening behavior
these must not break. _trello_get mocked so this stays fully offline --
no real Trello API call.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import urllib.error
from unittest.mock import patch

from saturday.trello_client import fetch_board_cards, fetch_list_id_to_name_map, fetch_card_current_state


def _list(list_id, name):
    return {"id": list_id, "name": name}


def _card(card_id, name, desc="", short_url="", date_last_activity=None, checklists=None):
    return {
        "id": card_id,
        "name": name,
        "desc": desc,
        "shortUrl": short_url,
        "dateLastActivity": date_last_activity,
        "checklists": checklists or [],
    }


def test_card_includes_last_activity_field():
    lists_response = [_list("list1", "Dump")]
    cards_response = [_card("card1", "A card", date_last_activity="2026-05-31T12:17:18.243Z")]

    with patch("saturday.trello_client._trello_get", side_effect=[lists_response, cards_response]):
        cards = fetch_board_cards(board_id="board1")

    assert len(cards) == 1
    assert cards[0]["last_activity"] == "2026-05-31T12:17:18.243Z"


def test_card_missing_date_last_activity_maps_to_none():
    """Trello always sends dateLastActivity in practice (live-verified), but
    fetch_board_cards should not crash if a card response ever omits it."""
    lists_response = [_list("list1", "Dump")]
    card_without_field = {"id": "card1", "name": "A card", "desc": "", "shortUrl": "", "checklists": []}

    with patch("saturday.trello_client._trello_get", side_effect=[lists_response, [card_without_field]]):
        cards = fetch_board_cards(board_id="board1")

    assert cards[0]["last_activity"] is None


def test_only_relevant_lists_fetched():
    lists_response = [_list("list1", "Dump"), _list("list2", "Done"), _list("list3", "In Progress")]
    dump_cards = [_card("card1", "Dump card", date_last_activity="2026-01-01T00:00:00.000Z")]
    in_progress_cards = [_card("card2", "In progress card", date_last_activity="2026-01-02T00:00:00.000Z")]

    with patch("saturday.trello_client._trello_get", side_effect=[lists_response, dump_cards, in_progress_cards]):
        cards = fetch_board_cards(board_id="board1")

    assert {c["card_id"] for c in cards} == {"card1", "card2"}
    assert {c["list_name"] for c in cards} == {"Dump", "In Progress"}


def test_checklist_items_flattened_alongside_last_activity():
    lists_response = [_list("list1", "Dump")]
    checklists = [{"checkItems": [{"name": "Step 1"}, {"name": "Step 2"}]}]
    cards_response = [_card("card1", "A card", date_last_activity="2026-05-31T12:17:18.243Z", checklists=checklists)]

    with patch("saturday.trello_client._trello_get", side_effect=[lists_response, cards_response]):
        cards = fetch_board_cards(board_id="board1")

    assert cards[0]["checklist_items"] == ["Step 1", "Step 2"]
    assert cards[0]["last_activity"] == "2026-05-31T12:17:18.243Z"


def test_full_card_shape():
    lists_response = [_list("list1", "Dump")]
    cards_response = [_card(
        "card1", "A card", desc="body text", short_url="https://trello.com/c/abc",
        date_last_activity="2026-05-31T12:17:18.243Z",
    )]

    with patch("saturday.trello_client._trello_get", side_effect=[lists_response, cards_response]):
        cards = fetch_board_cards(board_id="board1")

    assert cards[0] == {
        "card_id": "card1", "name": "A card", "desc": "body text",
        "list_id": "list1", "list_name": "Dump", "url": "https://trello.com/c/abc",
        "checklist_items": [], "last_activity": "2026-05-31T12:17:18.243Z",
    }


# ── fetch_list_id_to_name_map (sub-phase 4) ─────────────────────────────────

def test_list_id_to_name_map_includes_lists_outside_relevant_list_names():
    """Unlike fetch_board_cards, this includes every open list -- Done,
    and any other non-Dump/In-Progress list a card might have moved to."""
    lists_response = [
        _list("list1", "Dump"), _list("list2", "In Progress"),
        _list("list3", "Done"), _list("list4", "Future Ideas"),
    ]
    with patch("saturday.trello_client._trello_get", return_value=lists_response) as mock_get:
        result = fetch_list_id_to_name_map(board_id="board1")

    assert result == {"list1": "Dump", "list2": "In Progress", "list3": "Done", "list4": "Future Ideas"}
    mock_get.assert_called_once_with("/boards/board1/lists", {"filter": "open"})


# ── fetch_card_current_state (sub-phase 4) ──────────────────────────────────

def test_card_current_state_returns_real_fields():
    card_response = {"id": "card1", "name": "A card", "idList": "list2", "closed": False}
    with patch("saturday.trello_client._trello_get", return_value=card_response):
        result = fetch_card_current_state("card1")

    assert result == {"card_id": "card1", "name": "A card", "list_id": "list2", "closed": False}


def test_card_current_state_reflects_closed_flag():
    card_response = {"id": "card1", "name": "A card", "idList": "list3", "closed": True}
    with patch("saturday.trello_client._trello_get", return_value=card_response):
        result = fetch_card_current_state("card1")

    assert result["closed"] is True


def test_card_current_state_returns_none_on_404():
    error = urllib.error.HTTPError(url="x", code=404, msg="Not Found", hdrs=None, fp=None)
    with patch("saturday.trello_client._trello_get", side_effect=error):
        result = fetch_card_current_state("ghost-card")

    assert result is None


def test_card_current_state_reraises_non_404_errors():
    error = urllib.error.HTTPError(url="x", code=500, msg="Server Error", hdrs=None, fp=None)
    with patch("saturday.trello_client._trello_get", side_effect=error):
        try:
            fetch_card_current_state("card1")
            assert False, "expected HTTPError to propagate"
        except urllib.error.HTTPError as e:
            assert e.code == 500
