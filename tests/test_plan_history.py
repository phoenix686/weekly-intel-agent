"""
sunday/plan_history.py -- new module, Sunday plan LLM prioritization
checkpoint sub-phase 3. Covers record_plan_history()'s real behavior:
one entry per run_id, duplicate card_ids collapsed, real fields written.
get_store() mocked so this stays fully offline.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unittest.mock import patch

from sunday.plan_history import record_plan_history


class _FakeStore:
    def __init__(self):
        self.put_calls: list[tuple] = []

    def put(self, namespace, key, value):
        self.put_calls.append((namespace, key, value))


def test_writes_one_entry_keyed_by_run_id():
    fake_store = _FakeStore()
    with patch("sunday.plan_history.get_store", return_value=fake_store):
        record_plan_history("run-1", ["card1", "card2"])

    assert len(fake_store.put_calls) == 1
    namespace, key, value = fake_store.put_calls[0]
    assert namespace == ("weekly_intel", "plan_history")
    assert key == "run-1"
    assert value["run_id"] == "run-1"
    assert value["card_ids"] == ["card1", "card2"]
    assert "generated_at" in value


def test_duplicate_card_ids_collapsed():
    fake_store = _FakeStore()
    with patch("sunday.plan_history.get_store", return_value=fake_store):
        record_plan_history("run-1", ["card1", "card2", "card1"])

    _, _, value = fake_store.put_calls[0]
    assert value["card_ids"] == ["card1", "card2"]


def test_empty_card_ids_still_records_an_entry():
    """A week with zero surfaced project cards is real data (distinct
    from no record at all), not skipped."""
    fake_store = _FakeStore()
    with patch("sunday.plan_history.get_store", return_value=fake_store):
        record_plan_history("run-1", [])

    assert len(fake_store.put_calls) == 1
    _, _, value = fake_store.put_calls[0]
    assert value["card_ids"] == []


def test_card_ids_sorted_for_deterministic_output():
    fake_store = _FakeStore()
    with patch("sunday.plan_history.get_store", return_value=fake_store):
        record_plan_history("run-1", ["cardZ", "cardA"])

    _, _, value = fake_store.put_calls[0]
    assert value["card_ids"] == ["cardA", "cardZ"]
