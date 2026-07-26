"""
correlate_trello (saturday/nodes/correlate_trello.py) -- zero unit test
coverage existed before this file (confirmed by grep last session).
Covers the actual match/no-match judgment, the keep-filter (only
keep=True scored_items get correlated), and the JSON-parse-failure
fallback path -- not just "it runs without crashing". Real Anthropic
call and record_node_summary both mocked so this stays fully offline.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
from unittest.mock import patch, MagicMock

import saturday.nodes.correlate_trello as correlate_mod
from saturday.nodes.correlate_trello import correlate_trello


def _scored_item(url, keep=True, tags=None, reasoning="r"):
    return {"url": url, "title": "T", "text": "t", "keep": keep, "tags": tags or ["evals"], "reasoning": reasoning}


def _card(card_id, name, list_name="In Progress"):
    return {"card_id": card_id, "list_name": list_name, "name": name, "checklist_items": []}


def _state(scored_items, trello_cards, run_id="run-1"):
    return {
        "run_id": run_id, "scored_items": scored_items, "trello_cards": trello_cards,
        "correlated_items": [], "classified_items": [], "plan_text": "", "plan_item_map": {},
        "pending_approvals": [], "pending_resumes": [], "costs": [], "errors": [], "source_context": "saturday",
    }


def _haiku_response(matches: list[dict]):
    resp = MagicMock()
    resp.content = [MagicMock(text=json.dumps(matches))]
    resp.usage.input_tokens = 100
    resp.usage.output_tokens = 40
    return resp


def test_item_matched_to_specific_card():
    scored = [_scored_item("https://a.com/1")]
    cards = [_card("card-1", "Weekly Intel Agent build")]
    haiku_reply = [{"item_id": "https://a.com/1", "matched_card_id": "card-1", "match_reasoning": "directly about this project"}]

    with patch.object(correlate_mod.client.messages, "create", return_value=_haiku_response(haiku_reply)), \
         patch.object(correlate_mod, "record_node_summary"):
        result = correlate_trello(_state(scored, cards))

    assert result["correlated_items"][0]["matched_card_id"] == "card-1"


def test_item_not_matched_stays_null():
    scored = [_scored_item("https://a.com/1")]
    cards = [_card("card-1", "Some unrelated project")]
    haiku_reply = [{"item_id": "https://a.com/1", "matched_card_id": None, "match_reasoning": "no real connection"}]

    with patch.object(correlate_mod.client.messages, "create", return_value=_haiku_response(haiku_reply)), \
         patch.object(correlate_mod, "record_node_summary"):
        result = correlate_trello(_state(scored, cards))

    assert result["correlated_items"][0]["matched_card_id"] is None


def test_only_keep_true_items_are_correlated():
    scored = [
        _scored_item("https://a.com/1", keep=True),
        _scored_item("https://b.com/1", keep=False),
    ]
    cards = [_card("card-1", "Some project")]
    haiku_reply = [{"item_id": "https://a.com/1", "matched_card_id": None, "match_reasoning": "r"}]

    with patch.object(correlate_mod.client.messages, "create", return_value=_haiku_response(haiku_reply)) as mock_create, \
         patch.object(correlate_mod, "record_node_summary"):
        result = correlate_trello(_state(scored, cards))

    assert len(result["correlated_items"]) == 1
    assert result["correlated_items"][0]["url"] == "https://a.com/1"
    # the dropped (keep=False) item's url must never even reach the prompt
    prompt = mock_create.call_args.kwargs["messages"][0]["content"]
    assert "https://b.com/1" not in prompt


def test_json_parse_failure_fallback_sets_all_matched_card_id_null():
    scored = [_scored_item("https://a.com/1"), _scored_item("https://b.com/1")]
    cards = [_card("card-1", "Some project")]
    bad_resp = MagicMock()
    bad_resp.content = [MagicMock(text="not valid json")]
    bad_resp.usage.input_tokens = 10
    bad_resp.usage.output_tokens = 5

    with patch.object(correlate_mod.client.messages, "create", return_value=bad_resp), \
         patch.object(correlate_mod, "record_node_summary") as mock_summary:
        result = correlate_trello(_state(scored, cards))

    assert len(result["correlated_items"]) == 2
    assert all(i["matched_card_id"] is None for i in result["correlated_items"])
    assert "correlate_trello JSON parse failed after retry" in result["errors"][0]
    mock_summary.assert_called_once()
    _, kwargs = mock_summary.call_args
    assert kwargs["items_out"] == 0
    assert kwargs["error_summary"] == "JSON parse failed after retry"


def test_record_node_summary_reflects_matched_count_not_total_correlated():
    scored = [_scored_item("https://a.com/1"), _scored_item("https://b.com/1"), _scored_item("https://c.com/1")]
    cards = [_card("card-1", "Weekly Intel Agent build")]
    haiku_reply = [
        {"item_id": "https://a.com/1", "matched_card_id": "card-1", "match_reasoning": "r"},
        {"item_id": "https://b.com/1", "matched_card_id": None, "match_reasoning": "r"},
        {"item_id": "https://c.com/1", "matched_card_id": None, "match_reasoning": "r"},
    ]

    with patch.object(correlate_mod.client.messages, "create", return_value=_haiku_response(haiku_reply)), \
         patch.object(correlate_mod, "record_node_summary") as mock_summary:
        correlate_trello(_state(scored, cards))

    mock_summary.assert_called_once()
    _, kwargs = mock_summary.call_args
    assert kwargs["items_in"] == 3   # all kept items considered
    assert kwargs["items_out"] == 1  # only the real match, not all 3 correlated_items
