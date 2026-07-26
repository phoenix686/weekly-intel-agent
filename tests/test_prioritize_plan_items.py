"""
prioritize_plan_items (saturday/nodes/prioritize_plan_items.py) -- new node,
Saturday plan LLM prioritization checkpoint, sub-phase 5. Covers: bounding
at MAX_PROJECT_WORK_ITEMS regardless of what the model returns, dropping
hallucinated card_ids/item_urls, the new_item/stale_nudge distinction,
and the JSON-parse-failure fallback. Real Anthropic call and
record_node_summary both mocked so this stays fully offline.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
from unittest.mock import patch, MagicMock

import saturday.nodes.prioritize_plan_items as prioritize_mod
from saturday.nodes.prioritize_plan_items import prioritize_plan_items, MAX_PROJECT_WORK_ITEMS


def _matched_item(url, card_id, tags=None, reasoning="r"):
    return {
        "url": url, "title": "T", "text": "t", "reasoning": reasoning,
        "classification": "plan_item", "matched_card_id": card_id,
        "tags": tags or ["agentic-engineering"],
    }


def _card(card_id, name="A card", list_name="In Progress", last_activity="2026-07-01T00:00:00.000Z"):
    return {"card_id": card_id, "name": name, "list_name": list_name, "last_activity": last_activity}


def _movement(card_id, status, previous_list_name="In Progress", current_list_name="In Progress"):
    return {
        "card_id": card_id, "status": status,
        "previous_list_name": previous_list_name, "current_list_name": current_list_name,
    }


def _state(classified_items, trello_cards=None, card_movements=None, run_id="run-1"):
    return {
        "run_id": run_id, "classified_items": classified_items,
        "trello_cards": trello_cards or [], "card_movements": card_movements or [],
        "errors": [],
    }


def _haiku_response(selection: list[dict]):
    resp = MagicMock()
    resp.content = [MagicMock(text=json.dumps(selection))]
    resp.usage.input_tokens = 100
    resp.usage.output_tokens = 40
    return resp


def test_selected_card_appears_in_output():
    items = [_matched_item("https://a.com/1", "card1")]
    cards = [_card("card1")]
    reply = [{"matched_card_id": "card1", "source": "new_item", "item_url": "https://a.com/1",
              "priority_reasoning": "directly continues active work", "movement_note": None}]

    with patch.object(prioritize_mod.client.messages, "create", return_value=_haiku_response(reply)), \
         patch.object(prioritize_mod, "record_node_summary"):
        result = prioritize_plan_items(_state(items, cards))

    assert len(result["prioritized_project_work"]) == 1
    assert result["prioritized_project_work"][0]["matched_card_id"] == "card1"
    assert result["prioritized_project_work"][0]["source"] == "new_item"


def test_stale_nudge_entry_alongside_a_real_new_item():
    """A card can still be surfaced with source=stale_nudge and no
    corresponding matched item this week -- purely from board staleness --
    as long as the model call happens at all, i.e. at least one OTHER
    card has a real new_item match this week. (Step 6, 2026-07-22: the
    prior "0 new items -> pure stale_nudge backfill from the whole board"
    path is removed -- see the dedicated zero-matched-items tests below --
    but stale_nudge alongside a real match is unaffected.)"""
    items = [_matched_item("https://a.com/1", "card1")]
    cards = [_card("card1"), _card("card2")]
    reply = [
        {"matched_card_id": "card1", "source": "new_item", "item_url": "https://a.com/1",
         "priority_reasoning": "directly continues active work", "movement_note": None},
        {"matched_card_id": "card2", "source": "stale_nudge", "item_url": None,
         "priority_reasoning": "idle for 3 weeks", "movement_note": None},
    ]

    with patch.object(prioritize_mod.client.messages, "create", return_value=_haiku_response(reply)), \
         patch.object(prioritize_mod, "record_node_summary"):
        result = prioritize_plan_items(_state(items, cards))

    stale = next(e for e in result["prioritized_project_work"] if e["matched_card_id"] == "card2")
    assert stale["source"] == "stale_nudge"
    assert stale["item_url"] is None


def test_hard_cap_at_max_project_work_items_even_if_model_returns_more():
    items = [_matched_item("https://a.com/1", "card0")]
    cards = [_card(f"card{i}") for i in range(MAX_PROJECT_WORK_ITEMS + 3)]
    reply = [
        {"matched_card_id": f"card{i}", "source": "stale_nudge", "item_url": None,
         "priority_reasoning": "r", "movement_note": None}
        for i in range(MAX_PROJECT_WORK_ITEMS + 3)
    ]

    with patch.object(prioritize_mod.client.messages, "create", return_value=_haiku_response(reply)), \
         patch.object(prioritize_mod, "record_node_summary"):
        result = prioritize_plan_items(_state(items, cards))

    assert len(result["prioritized_project_work"]) == MAX_PROJECT_WORK_ITEMS


def test_hallucinated_card_id_dropped():
    items = [_matched_item("https://a.com/1", "card1")]
    cards = [_card("card1")]
    reply = [
        {"matched_card_id": "card1", "source": "new_item", "item_url": "https://a.com/1", "priority_reasoning": "r", "movement_note": None},
        {"matched_card_id": "ghost-card-not-real", "source": "stale_nudge", "item_url": None, "priority_reasoning": "r", "movement_note": None},
    ]

    with patch.object(prioritize_mod.client.messages, "create", return_value=_haiku_response(reply)), \
         patch.object(prioritize_mod, "record_node_summary"):
        result = prioritize_plan_items(_state(items, cards))

    assert len(result["prioritized_project_work"]) == 1
    assert result["prioritized_project_work"][0]["matched_card_id"] == "card1"


def test_hallucinated_item_url_on_new_item_dropped():
    items = [_matched_item("https://a.com/1", "card1")]
    cards = [_card("card1")]
    reply = [{"matched_card_id": "card1", "source": "new_item", "item_url": "https://not-real.com",
              "priority_reasoning": "r", "movement_note": None}]

    with patch.object(prioritize_mod.client.messages, "create", return_value=_haiku_response(reply)), \
         patch.object(prioritize_mod, "record_node_summary"):
        result = prioritize_plan_items(_state(items, cards))

    assert result["prioritized_project_work"] == []


def test_order_preserved_from_model_response():
    items = [_matched_item("https://a.com/1", "card1")]
    cards = [_card("card1"), _card("card2")]
    reply = [
        {"matched_card_id": "card2", "source": "stale_nudge", "item_url": None, "priority_reasoning": "higher priority", "movement_note": None},
        {"matched_card_id": "card1", "source": "new_item", "item_url": "https://a.com/1", "priority_reasoning": "lower priority", "movement_note": None},
    ]

    with patch.object(prioritize_mod.client.messages, "create", return_value=_haiku_response(reply)), \
         patch.object(prioritize_mod, "record_node_summary"):
        result = prioritize_plan_items(_state(items, cards))

    assert [e["matched_card_id"] for e in result["prioritized_project_work"]] == ["card2", "card1"]


def test_movement_block_included_in_prompt():
    items = [_matched_item("https://a.com/1", "card1")]
    cards = [_card("card1")]
    movements = [_movement("card1", "unchanged")]
    reply = [{"matched_card_id": "card1", "source": "new_item", "item_url": "https://a.com/1",
              "priority_reasoning": "r", "movement_note": "unchanged since last week"}]

    with patch.object(prioritize_mod.client.messages, "create", return_value=_haiku_response(reply)) as mock_create, \
         patch.object(prioritize_mod, "record_node_summary"):
        prioritize_plan_items(_state(items, cards, card_movements=movements))

    prompt = mock_create.call_args.kwargs["messages"][0]["content"]
    assert "card1" in prompt and "unchanged" in prompt


def test_json_parse_failure_falls_back_to_unprioritized_matched_items():
    items = [_matched_item("https://a.com/1", "card1"), _matched_item("https://b.com/1", "card2")]
    cards = [_card("card1"), _card("card2")]
    bad_resp = MagicMock()
    bad_resp.content = [MagicMock(text="not valid json")]
    bad_resp.usage.input_tokens = 10
    bad_resp.usage.output_tokens = 5

    with patch.object(prioritize_mod.client.messages, "create", return_value=bad_resp), \
         patch.object(prioritize_mod, "record_node_summary") as mock_summary:
        result = prioritize_plan_items(_state(items, cards))

    assert len(result["prioritized_project_work"]) == 2
    assert all(e["source"] == "new_item" for e in result["prioritized_project_work"])
    assert "prioritize_plan_items JSON parse failed after retry" in result["errors"][0]
    mock_summary.assert_called_once()


def test_course_tagged_items_excluded_from_candidates():
    """A course-tagged item never counts as a matched candidate (it
    belongs in the Courses section elsewhere, not Existing Project Work)
    -- proven here alongside a real, non-course matched item so this test
    still reaches the LLM call (an all-course classified_items list would
    otherwise hit the zero-matched-items skip path below, which wouldn't
    isolate this exclusion from that separate behavior)."""
    items = [
        _matched_item("https://a.com/1", "card1", tags=["course"]),
        _matched_item("https://b.com/1", "card2"),
    ]
    cards = [_card("card1"), _card("card2")]
    reply = [{"matched_card_id": "card2", "source": "new_item", "item_url": "https://b.com/1",
              "priority_reasoning": "r", "movement_note": None}]

    with patch.object(prioritize_mod.client.messages, "create", return_value=_haiku_response(reply)) as mock_create, \
         patch.object(prioritize_mod, "record_node_summary"):
        prioritize_plan_items(_state(items, cards))

    prompt = mock_create.call_args.kwargs["messages"][0]["content"]
    assert "https://a.com/1" not in prompt
    assert "https://b.com/1" in prompt


def test_zero_matched_items_skips_llm_call_entirely():
    """Step 6 (2026-07-22): 0 new plan_item(s) matched to a Trello card
    this week -- the LLM must never be called, and no board cards enter
    into consideration, even though the board itself has real cards."""
    cards = [_card("card1"), _card("card2")]

    with patch.object(prioritize_mod.client.messages, "create") as mock_create, \
         patch.object(prioritize_mod, "record_node_summary") as mock_summary:
        result = prioritize_plan_items(_state([], cards))

    mock_create.assert_not_called()
    assert result["prioritized_project_work"] == []
    assert result["costs"][0]["cost_usd"] == 0.0
    assert result["costs"][0]["input_tokens"] == 0
    assert result["costs"][0]["output_tokens"] == 0
    mock_summary.assert_called_once()
    assert mock_summary.call_args.kwargs["items_in"] == 0
    assert mock_summary.call_args.kwargs["items_out"] == 0


def test_zero_matched_items_produces_no_trello_cards_in_output_even_with_a_large_board():
    """Directly proves no board card leaks into the plan as a substitute
    for real new content -- previously this exact scenario (0 matched
    items, a real non-empty board) would have called Haiku and backfilled
    Existing Project Work with pure stale_nudge picks."""
    cards = [_card(f"card{i}") for i in range(10)]

    with patch.object(prioritize_mod.client.messages, "create") as mock_create, \
         patch.object(prioritize_mod, "record_node_summary"):
        result = prioritize_plan_items(_state([], cards))

    mock_create.assert_not_called()
    assert result["prioritized_project_work"] == []
