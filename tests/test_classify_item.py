"""
classification-decision-logging (Checkpoint 4, closeout-spec.md Section 4
point 1): classify_item.py logs EVERY decision -- plan_item and
project_proposal alike, not just proposals -- to the store under
namespace=("weekly_intel","classification_log"). Closes the blind spot
where plan_item decisions (the majority of all items, since they bypass
the approval gate by design) left zero trace.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
from unittest.mock import patch, MagicMock

import sunday.nodes.classify_item as classify_item_mod
from sunday.nodes.classify_item import classify_item, _CLASSIFICATION_LOG_NAMESPACE


class _FakeStore:
    def __init__(self, raise_on_put=False):
        self.puts: list[tuple] = []
        self._raise_on_put = raise_on_put

    def put(self, namespace, key, value):
        if self._raise_on_put:
            raise RuntimeError("simulated store failure")
        self.puts.append((namespace, key, value))


def _haiku_response(classifications: list[dict]):
    resp = MagicMock()
    resp.content = [MagicMock(text=json.dumps(classifications))]
    resp.usage.input_tokens = 100
    resp.usage.output_tokens = 40
    return resp


def _state(correlated_items):
    return {
        "run_id": "run-classify-1",
        "scored_items": [], "trello_cards": [], "correlated_items": correlated_items,
        "classified_items": [], "plan_text": "", "plan_item_map": {}, "pending_approvals": [],
        "pending_resumes": [], "costs": [], "errors": [], "source_context": "sunday",
    }


def test_classification_log_written_for_both_plan_item_and_proposal():
    fake_store = _FakeStore()
    correlated_items = [
        {"url": "https://example.com/plan-item", "matched_card_id": None, "tags": ["agentic-engineering"], "reasoning": "routine reading"},
        {"url": "https://example.com/proposal", "matched_card_id": None, "tags": ["memory-systems"], "reasoning": "new idea"},
    ]
    haiku_reply = [
        {"item_id": "https://example.com/plan-item", "classification": "plan_item", "proposal_type": None, "classification_reasoning": "routine"},
        {"item_id": "https://example.com/proposal", "classification": "project_proposal", "proposal_type": "new", "classification_reasoning": "structurally new"},
    ]

    with patch.object(classify_item_mod, "get_store", return_value=fake_store), \
         patch.object(classify_item_mod.client.messages, "create", return_value=_haiku_response(haiku_reply)):
        result = classify_item(_state(correlated_items))

    log_puts = [p for p in fake_store.puts if p[0] == _CLASSIFICATION_LOG_NAMESPACE]
    assert len(log_puts) == 2, f"expected one classification_log entry per item (both types), got {len(log_puts)}"

    by_item_id = {p[2]["item_id"]: p[2] for p in log_puts}
    assert by_item_id["https://example.com/plan-item"]["decision"] == "plan_item"
    assert by_item_id["https://example.com/plan-item"]["proposal_type"] is None
    assert by_item_id["https://example.com/plan-item"]["run_id"] == "run-classify-1"
    assert by_item_id["https://example.com/proposal"]["decision"] == "project_proposal"
    assert by_item_id["https://example.com/proposal"]["proposal_type"] == "new"

    # Real return value still correct regardless of the new logging side effect
    assert len(result["classified_items"]) == 2
    assert len(result["pending_approvals"]) == 1


def test_failed_store_write_does_not_affect_node_return_value():
    fake_store = _FakeStore(raise_on_put=True)
    correlated_items = [
        {"url": "https://example.com/x", "matched_card_id": None, "tags": [], "reasoning": "r"},
    ]
    haiku_reply = [
        {"item_id": "https://example.com/x", "classification": "plan_item", "proposal_type": None, "classification_reasoning": "r"},
    ]

    with patch.object(classify_item_mod, "get_store", return_value=fake_store), \
         patch.object(classify_item_mod.client.messages, "create", return_value=_haiku_response(haiku_reply)):
        result = classify_item(_state(correlated_items))  # must not raise

    assert len(result["classified_items"]) == 1
    assert result["classified_items"][0]["classification"] == "plan_item"
    assert result["pending_approvals"] == []


def test_json_parse_failure_fallback_path_still_logs_classifications():
    """Even the degraded all-plan_item fallback path (JSON parse failed
    twice) is a real classification decision for every item and must
    still be logged -- not skipped just because it's a failure path."""
    fake_store = _FakeStore()
    correlated_items = [
        {"url": "https://example.com/a", "matched_card_id": None, "tags": [], "reasoning": "r"},
        {"url": "https://example.com/b", "matched_card_id": None, "tags": [], "reasoning": "r"},
    ]
    bad_resp = MagicMock()
    bad_resp.content = [MagicMock(text="not valid json at all")]
    bad_resp.usage.input_tokens = 10
    bad_resp.usage.output_tokens = 5

    with patch.object(classify_item_mod, "get_store", return_value=fake_store), \
         patch.object(classify_item_mod.client.messages, "create", return_value=bad_resp):
        result = classify_item(_state(correlated_items))

    log_puts = [p for p in fake_store.puts if p[0] == _CLASSIFICATION_LOG_NAMESPACE]
    assert len(log_puts) == 2
    assert all(p[2]["decision"] == "plan_item" for p in log_puts)
    assert len(result["classified_items"]) == 2
    assert "classify_item JSON parse failed after retry" in result["errors"][0]
