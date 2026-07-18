"""
sunday/plan_history.py -- Sunday plan LLM prioritization checkpoint,
sub-phase 3 (record_plan_history), schema revised + get_most_recent_prior_entry
added in sub-phase 4. Covers: one entry per run_id, duplicate card_ids
collapsed (keeping first list_name), real fields written, and finding the
most recent prior entry while excluding the current run_id. get_store()
mocked so this stays fully offline.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unittest.mock import patch

from sunday.plan_history import record_plan_history, get_most_recent_prior_entry


class _Item:
    def __init__(self, value):
        self.value = value


class _FakeStore:
    def __init__(self, seed: list[dict] | None = None):
        self._entries = list(seed or [])
        self.put_calls: list[tuple] = []

    def put(self, namespace, key, value):
        self.put_calls.append((namespace, key, value))

    def search(self, namespace, limit=1000):
        return [_Item(e) for e in self._entries][:limit]


def _card(card_id, list_name="In Progress"):
    return {"card_id": card_id, "list_name": list_name}


# ── record_plan_history ─────────────────────────────────────────────────────

def test_writes_one_entry_keyed_by_run_id():
    fake_store = _FakeStore()
    with patch("sunday.plan_history.get_store", return_value=fake_store):
        record_plan_history("run-1", [_card("card1"), _card("card2", "Dump")])

    assert len(fake_store.put_calls) == 1
    namespace, key, value = fake_store.put_calls[0]
    assert namespace == ("weekly_intel", "plan_history")
    assert key == "run-1"
    assert value["run_id"] == "run-1"
    assert value["cards"] == [
        {"card_id": "card1", "list_name": "In Progress"},
        {"card_id": "card2", "list_name": "Dump"},
    ]
    assert "generated_at" in value


def test_duplicate_card_ids_collapsed_keeping_first_list_name():
    fake_store = _FakeStore()
    with patch("sunday.plan_history.get_store", return_value=fake_store):
        record_plan_history("run-1", [_card("card1", "In Progress"), _card("card1", "Done")])

    _, _, value = fake_store.put_calls[0]
    assert value["cards"] == [{"card_id": "card1", "list_name": "In Progress"}]


def test_empty_cards_still_records_an_entry():
    """A week with zero surfaced project cards is real data (distinct
    from no record at all), not skipped."""
    fake_store = _FakeStore()
    with patch("sunday.plan_history.get_store", return_value=fake_store):
        record_plan_history("run-1", [])

    assert len(fake_store.put_calls) == 1
    _, _, value = fake_store.put_calls[0]
    assert value["cards"] == []


def test_cards_sorted_by_card_id_for_deterministic_output():
    fake_store = _FakeStore()
    with patch("sunday.plan_history.get_store", return_value=fake_store):
        record_plan_history("run-1", [_card("cardZ"), _card("cardA")])

    _, _, value = fake_store.put_calls[0]
    assert [c["card_id"] for c in value["cards"]] == ["cardA", "cardZ"]


# ── get_most_recent_prior_entry ─────────────────────────────────────────────

def test_returns_none_when_no_entries_exist():
    fake_store = _FakeStore(seed=[])
    with patch("sunday.plan_history.get_store", return_value=fake_store):
        result = get_most_recent_prior_entry(current_run_id="run-2")

    assert result is None


def test_returns_latest_entry_by_generated_at():
    fake_store = _FakeStore(seed=[
        {"run_id": "run-1", "cards": [_card("card1")], "generated_at": "2026-07-05T00:00:00+00:00"},
        {"run_id": "run-2", "cards": [_card("card2")], "generated_at": "2026-07-12T00:00:00+00:00"},
    ])
    with patch("sunday.plan_history.get_store", return_value=fake_store):
        result = get_most_recent_prior_entry(current_run_id="run-3")

    assert result["run_id"] == "run-2"


def test_excludes_current_run_id():
    """Defensive: the current run's own entry (if it somehow already
    exists, e.g. a retry) is never treated as its own 'prior' entry."""
    fake_store = _FakeStore(seed=[
        {"run_id": "run-1", "cards": [_card("card1")], "generated_at": "2026-07-05T00:00:00+00:00"},
        {"run_id": "run-2", "cards": [_card("card2")], "generated_at": "2026-07-19T00:00:00+00:00"},
    ])
    with patch("sunday.plan_history.get_store", return_value=fake_store):
        result = get_most_recent_prior_entry(current_run_id="run-2")

    assert result["run_id"] == "run-1"
