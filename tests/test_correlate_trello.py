"""
correlate_trello (saturday/nodes/correlate_trello.py) -- zero unit test
coverage existed before this file (confirmed by grep last session).
Covers the actual match/no-match judgment, the keep-filter (only
keep=True scored_items get correlated), and the malformed-response /
Groq-API-failure fallback paths -- not just "it runs without crashing".
Real Groq call and record_node_summary both mocked so this stays fully
offline.

2026-07-26: switched from Anthropic/Haiku to Groq (openai/gpt-oss-120b)
with native structured outputs. Mocks get_groq_client() rather than
client.messages.create -- the fence-stripping retry-on-parse-failure path
is gone (strict:true guarantees schema-conformant JSON).
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
from unittest.mock import patch, MagicMock

import groq

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


def _groq_response(matches: list[dict]):
    resp = MagicMock()
    resp.choices = [MagicMock(message=MagicMock(content=json.dumps({"results": matches})))]
    resp.usage.prompt_tokens = 100
    resp.usage.completion_tokens = 40
    return resp


def _fake_groq_client(response=None, side_effect=None):
    fake_client = MagicMock()
    if side_effect is not None:
        fake_client.chat.completions.create.side_effect = side_effect
    else:
        fake_client.chat.completions.create.return_value = response
    return fake_client


def test_item_matched_to_specific_card():
    scored = [_scored_item("https://a.com/1")]
    cards = [_card("card-1", "Weekly Intel Agent build")]
    groq_reply = [{"item_id": "https://a.com/1", "matched_card_id": "card-1", "match_reasoning": "directly about this project"}]

    with patch.object(correlate_mod, "get_groq_client", return_value=_fake_groq_client(_groq_response(groq_reply))), \
         patch.object(correlate_mod, "record_node_summary"):
        result = correlate_trello(_state(scored, cards))

    assert result["correlated_items"][0]["matched_card_id"] == "card-1"
    assert result["costs"][0]["provider"] == "groq"


def test_item_not_matched_stays_null():
    scored = [_scored_item("https://a.com/1")]
    cards = [_card("card-1", "Some unrelated project")]
    groq_reply = [{"item_id": "https://a.com/1", "matched_card_id": None, "match_reasoning": "no real connection"}]

    with patch.object(correlate_mod, "get_groq_client", return_value=_fake_groq_client(_groq_response(groq_reply))), \
         patch.object(correlate_mod, "record_node_summary"):
        result = correlate_trello(_state(scored, cards))

    assert result["correlated_items"][0]["matched_card_id"] is None


def test_only_keep_true_items_are_correlated():
    scored = [
        _scored_item("https://a.com/1", keep=True),
        _scored_item("https://b.com/1", keep=False),
    ]
    cards = [_card("card-1", "Some project")]
    groq_reply = [{"item_id": "https://a.com/1", "matched_card_id": None, "match_reasoning": "r"}]

    fake_client = _fake_groq_client(_groq_response(groq_reply))
    with patch.object(correlate_mod, "get_groq_client", return_value=fake_client), \
         patch.object(correlate_mod, "record_node_summary"):
        result = correlate_trello(_state(scored, cards))

    assert len(result["correlated_items"]) == 1
    assert result["correlated_items"][0]["url"] == "https://a.com/1"
    # the dropped (keep=False) item's url must never even reach the prompt
    prompt = fake_client.chat.completions.create.call_args.kwargs["messages"][0]["content"]
    assert "https://b.com/1" not in prompt


def test_malformed_structured_output_fallback_sets_all_matched_card_id_null():
    scored = [_scored_item("https://a.com/1"), _scored_item("https://b.com/1")]
    cards = [_card("card-1", "Some project")]
    bad_resp = MagicMock()
    bad_resp.choices = [MagicMock(message=MagicMock(content="not valid json"))]
    bad_resp.usage.prompt_tokens = 10
    bad_resp.usage.completion_tokens = 5

    with patch.object(correlate_mod, "get_groq_client", return_value=_fake_groq_client(bad_resp)), \
         patch.object(correlate_mod, "record_node_summary") as mock_summary:
        result = correlate_trello(_state(scored, cards))

    assert len(result["correlated_items"]) == 2
    assert all(i["matched_card_id"] is None for i in result["correlated_items"])
    assert "correlate_trello malformed structured-output response" in result["errors"][0]
    mock_summary.assert_called_once()
    _, kwargs = mock_summary.call_args
    assert kwargs["items_out"] == 0
    assert kwargs["error_summary"] == "malformed structured-output response"


def test_groq_api_failure_after_retries_falls_back_to_all_null():
    """A groq.APIError surfacing here means the SDK's own client-side
    timeout/retry (core/groq_client.py) already exhausted every attempt --
    the node must still degrade gracefully rather than let the exception
    propagate and take down the whole Saturday graph run."""
    scored = [_scored_item("https://a.com/1")]
    cards = [_card("card-1", "Some project")]

    with patch.object(correlate_mod, "get_groq_client",
                       return_value=_fake_groq_client(side_effect=groq.APIConnectionError(request=MagicMock()))), \
         patch.object(correlate_mod, "record_node_summary"):
        result = correlate_trello(_state(scored, cards))  # must not raise

    assert result["correlated_items"][0]["matched_card_id"] is None
    assert "correlate_trello Groq call failed after retries" in result["errors"][0]
    assert result["costs"][0]["cost_usd"] == 0.0


def test_record_node_summary_reflects_matched_count_not_total_correlated():
    scored = [_scored_item("https://a.com/1"), _scored_item("https://b.com/1"), _scored_item("https://c.com/1")]
    cards = [_card("card-1", "Weekly Intel Agent build")]
    groq_reply = [
        {"item_id": "https://a.com/1", "matched_card_id": "card-1", "match_reasoning": "r"},
        {"item_id": "https://b.com/1", "matched_card_id": None, "match_reasoning": "r"},
        {"item_id": "https://c.com/1", "matched_card_id": None, "match_reasoning": "r"},
    ]

    with patch.object(correlate_mod, "get_groq_client", return_value=_fake_groq_client(_groq_response(groq_reply))), \
         patch.object(correlate_mod, "record_node_summary") as mock_summary:
        correlate_trello(_state(scored, cards))

    mock_summary.assert_called_once()
    _, kwargs = mock_summary.call_args
    assert kwargs["items_in"] == 3   # all kept items considered
    assert kwargs["items_out"] == 1  # only the real match, not all 3 correlated_items
