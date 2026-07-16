import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
from unittest.mock import patch, MagicMock

import sunday.same_day_nudge as same_day_nudge
from sunday.same_day_nudge import apply_nudge


class _Item:
    def __init__(self, key, value):
        self.key = key
        self.value = value


class _FakeStore:
    def __init__(self):
        self._data: dict = {}

    def get(self, namespace, key):
        if key in self._data:
            return _Item(key, self._data[key])
        return None

    def put(self, namespace, key, value):
        self._data[key] = value


def _haiku_response(direction: str, magnitude: str):
    resp = MagicMock()
    resp.content = [MagicMock(text=json.dumps({"direction": direction, "magnitude": magnitude}))]
    resp.usage.input_tokens = 50
    resp.usage.output_tokens = 10
    return resp


def test_three_mild_positive_reactions_sum_below_cap():
    fake_store = _FakeStore()
    with patch.object(same_day_nudge, "get_store", return_value=fake_store), \
         patch.object(same_day_nudge._client.messages, "create", return_value=_haiku_response("up", "mild")):
        for i in range(3):
            apply_nudge(f"https://example.com/{i}", "liked it", ["agentic-engineering"], "run-1")

    key = list(fake_store._data.keys())[0]
    assert fake_store._data[key]["cumulative_adjustment"] == 0.15  # 0.05 * 3, below +/-0.3 cap
    assert len(fake_store._data[key]["item_ids_contributing"]) == 3


def test_fourth_strong_reaction_clamps_at_cap_not_beyond():
    fake_store = _FakeStore()
    with patch.object(same_day_nudge, "get_store", return_value=fake_store):
        with patch.object(same_day_nudge._client.messages, "create", return_value=_haiku_response("up", "mild")):
            for i in range(3):
                apply_nudge(f"https://example.com/{i}", "liked it", ["agentic-engineering"], "run-1")
        # cumulative so far: 0.15. One more "strong" (+0.20) would push to 0.35 -- must clamp at 0.3.
        with patch.object(same_day_nudge._client.messages, "create", return_value=_haiku_response("up", "strong")):
            apply_nudge("https://example.com/4", "loved it!!", ["agentic-engineering"], "run-1")

    key = list(fake_store._data.keys())[0]
    assert fake_store._data[key]["cumulative_adjustment"] == 0.3
    assert fake_store._data[key]["cumulative_adjustment"] != 0.35


def test_different_tag_is_unaffected():
    fake_store = _FakeStore()
    with patch.object(same_day_nudge, "get_store", return_value=fake_store):
        with patch.object(same_day_nudge._client.messages, "create", return_value=_haiku_response("up", "strong")):
            apply_nudge("https://example.com/1", "loved it", ["agentic-engineering"], "run-1")
        with patch.object(same_day_nudge._client.messages, "create", return_value=_haiku_response("down", "strong")):
            apply_nudge("https://example.com/2", "hated it", ["memory-systems"], "run-1")

    assert len(fake_store._data) == 2
    values = list(fake_store._data.values())
    agentic = next(v for v in values if v["tag"] == "agentic-engineering")
    memory = next(v for v in values if v["tag"] == "memory-systems")
    assert agentic["cumulative_adjustment"] == 0.20
    assert memory["cumulative_adjustment"] == -0.20


def test_neutral_direction_applies_zero_delta():
    fake_store = _FakeStore()
    with patch.object(same_day_nudge, "get_store", return_value=fake_store), \
         patch.object(same_day_nudge._client.messages, "create", return_value=_haiku_response("neutral", "mild")):
        apply_nudge("https://example.com/1", "not sure", ["evals"], "run-1")

    key = list(fake_store._data.keys())[0]
    assert fake_store._data[key]["cumulative_adjustment"] == 0.0


def test_no_tags_skips_classification_entirely():
    fake_store = _FakeStore()
    with patch.object(same_day_nudge, "get_store", return_value=fake_store), \
         patch.object(same_day_nudge._client.messages, "create") as mock_create:
        costs = apply_nudge("https://example.com/1", "feedback text", [], "run-1")

    mock_create.assert_not_called()
    assert len(costs) == 1
    assert "no tags" in costs[0]["error"]
    assert fake_store._data == {}


def test_classification_failure_applies_no_adjustment():
    fake_store = _FakeStore()
    with patch.object(same_day_nudge, "get_store", return_value=fake_store), \
         patch.object(same_day_nudge._client.messages, "create", side_effect=RuntimeError("API down")):
        costs = apply_nudge("https://example.com/1", "feedback", ["evals"], "run-1")

    assert fake_store._data == {}  # no adjustment written
    assert any("classification failed" in c["error"] for c in costs)


def test_invalid_classification_shape_applies_no_adjustment():
    fake_store = _FakeStore()
    resp = MagicMock()
    resp.content = [MagicMock(text=json.dumps({"direction": "sideways", "magnitude": "huge"}))]
    resp.usage.input_tokens = 10
    resp.usage.output_tokens = 5

    with patch.object(same_day_nudge, "get_store", return_value=fake_store), \
         patch.object(same_day_nudge._client.messages, "create", return_value=resp):
        costs = apply_nudge("https://example.com/1", "feedback", ["evals"], "run-1")

    assert fake_store._data == {}
    assert any("invalid classification shape" in c["error"] for c in costs)
