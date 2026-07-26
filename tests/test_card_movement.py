"""
saturday/card_movement.py -- new module, Saturday plan LLM prioritization
checkpoint sub-phase 4. Covers detect_card_movement()'s real
classification logic (unchanged/moved/completed/archived/not_found)
against a mocked prior plan_history entry and mocked Trello responses.
No real HTTP/store call in this file.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unittest.mock import patch

from saturday.card_movement import detect_card_movement

_LIST_MAP = {"list_dump": "Dump", "list_in_progress": "In Progress", "list_done": "Done"}


def _prior_entry(cards, run_id="prior-run"):
    return {"run_id": run_id, "cards": cards, "generated_at": "2026-07-12T00:00:00+00:00"}


def test_returns_empty_list_when_no_prior_entry():
    with patch("saturday.card_movement.get_most_recent_prior_entry", return_value=None):
        result = detect_card_movement("run-current")

    assert result == []


def test_card_unchanged_when_still_in_same_list():
    prior = _prior_entry([{"card_id": "card1", "list_name": "In Progress"}])
    current_state = {"card_id": "card1", "name": "x", "list_id": "list_in_progress", "closed": False}

    with patch("saturday.card_movement.get_most_recent_prior_entry", return_value=prior), \
         patch("saturday.card_movement.fetch_list_id_to_name_map", return_value=_LIST_MAP), \
         patch("saturday.card_movement.fetch_card_current_state", return_value=current_state):
        result = detect_card_movement("run-current")

    assert result == [{
        "card_id": "card1", "previous_list_name": "In Progress",
        "current_list_name": "In Progress", "status": "unchanged",
    }]


def test_card_moved_to_a_different_non_done_list():
    prior = _prior_entry([{"card_id": "card1", "list_name": "Dump"}])
    current_state = {"card_id": "card1", "name": "x", "list_id": "list_in_progress", "closed": False}

    with patch("saturday.card_movement.get_most_recent_prior_entry", return_value=prior), \
         patch("saturday.card_movement.fetch_list_id_to_name_map", return_value=_LIST_MAP), \
         patch("saturday.card_movement.fetch_card_current_state", return_value=current_state):
        result = detect_card_movement("run-current")

    assert result[0]["status"] == "moved"
    assert result[0]["current_list_name"] == "In Progress"


def test_card_completed_when_moved_to_done_list():
    prior = _prior_entry([{"card_id": "card1", "list_name": "In Progress"}])
    current_state = {"card_id": "card1", "name": "x", "list_id": "list_done", "closed": False}

    with patch("saturday.card_movement.get_most_recent_prior_entry", return_value=prior), \
         patch("saturday.card_movement.fetch_list_id_to_name_map", return_value=_LIST_MAP), \
         patch("saturday.card_movement.fetch_card_current_state", return_value=current_state):
        result = detect_card_movement("run-current")

    assert result[0]["status"] == "completed"
    assert result[0]["current_list_name"] == "Done"


def test_card_archived_when_closed_flag_is_true():
    """closed=True takes priority over list_name -- a card can be
    archived while its idList still points at its old list."""
    prior = _prior_entry([{"card_id": "card1", "list_name": "In Progress"}])
    current_state = {"card_id": "card1", "name": "x", "list_id": "list_in_progress", "closed": True}

    with patch("saturday.card_movement.get_most_recent_prior_entry", return_value=prior), \
         patch("saturday.card_movement.fetch_list_id_to_name_map", return_value=_LIST_MAP), \
         patch("saturday.card_movement.fetch_card_current_state", return_value=current_state):
        result = detect_card_movement("run-current")

    assert result[0]["status"] == "archived"


def test_card_not_found_when_permanently_deleted():
    prior = _prior_entry([{"card_id": "card1", "list_name": "In Progress"}])

    with patch("saturday.card_movement.get_most_recent_prior_entry", return_value=prior), \
         patch("saturday.card_movement.fetch_list_id_to_name_map", return_value=_LIST_MAP), \
         patch("saturday.card_movement.fetch_card_current_state", return_value=None):
        result = detect_card_movement("run-current")

    assert result == [{
        "card_id": "card1", "previous_list_name": "In Progress",
        "current_list_name": None, "status": "not_found",
    }]


def test_multiple_cards_classified_independently():
    prior = _prior_entry([
        {"card_id": "card1", "list_name": "In Progress"},
        {"card_id": "card2", "list_name": "Dump"},
    ])
    states = {
        "card1": {"card_id": "card1", "name": "x", "list_id": "list_in_progress", "closed": False},
        "card2": {"card_id": "card2", "name": "y", "list_id": "list_done", "closed": False},
    }

    with patch("saturday.card_movement.get_most_recent_prior_entry", return_value=prior), \
         patch("saturday.card_movement.fetch_list_id_to_name_map", return_value=_LIST_MAP), \
         patch("saturday.card_movement.fetch_card_current_state", side_effect=lambda cid: states[cid]):
        result = detect_card_movement("run-current")

    statuses = {r["card_id"]: r["status"] for r in result}
    assert statuses == {"card1": "unchanged", "card2": "completed"}
