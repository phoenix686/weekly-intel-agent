import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unittest.mock import patch

from saturday.nodes.process_adhoc_input import process_adhoc_input


class _FakeItem:
    def __init__(self, key, value):
        self.key = key
        self.value = value


class _FakeStore:
    def __init__(self, entries: dict):
        self._entries = dict(entries)
        self.deleted: list[str] = []

    def search(self, namespace, limit=200):
        return [_FakeItem(k, v) for k, v in self._entries.items()][:limit]

    def delete(self, namespace, key):
        self.deleted.append(key)
        self._entries.pop(key, None)


def test_queued_message_produces_one_raw_item_with_text_preserved():
    fake_store = _FakeStore({
        "key-1": {"text": "check out this new agent framework", "queued_at": "2026-07-12T00:00:00+00:00"},
    })
    with patch("saturday.nodes.process_adhoc_input.get_store", return_value=fake_store):
        result = process_adhoc_input({})

    assert len(result["raw_items"]) == 1
    item = result["raw_items"][0]
    assert item["text"] == "check out this new agent framework"
    assert item["source"] == "adhoc_telegram"
    assert fake_store.deleted == ["key-1"]


def test_empty_queue_produces_no_raw_items():
    fake_store = _FakeStore({})
    with patch("saturday.nodes.process_adhoc_input.get_store", return_value=fake_store):
        result = process_adhoc_input({})

    assert result["raw_items"] == []
    assert len(result["costs"]) == 1
    assert result["costs"][0]["node_name"] == "process_adhoc_input"


def test_blank_text_entries_are_skipped_but_not_deleted():
    fake_store = _FakeStore({"key-blank": {"text": "   ", "queued_at": "2026-07-12T00:00:00+00:00"}})
    with patch("saturday.nodes.process_adhoc_input.get_store", return_value=fake_store):
        result = process_adhoc_input({})

    assert result["raw_items"] == []
    assert fake_store.deleted == []


def test_multiple_queued_messages_each_produce_a_raw_item():
    fake_store = _FakeStore({
        "key-1": {"text": "first item", "queued_at": "2026-07-12T00:00:00+00:00"},
        "key-2": {"text": "second item", "queued_at": "2026-07-12T01:00:00+00:00"},
    })
    with patch("saturday.nodes.process_adhoc_input.get_store", return_value=fake_store):
        result = process_adhoc_input({})

    assert len(result["raw_items"]) == 2
    texts = {item["text"] for item in result["raw_items"]}
    assert texts == {"first item", "second item"}
    assert sorted(fake_store.deleted) == ["key-1", "key-2"]


def test_process_adhoc_input_wired_saturday_only_not_daily():
    """discovery/graph.py now uses a single subgraph with a real
    route_sources() conditional entry point, not a two-graph-shape
    factory -- process_adhoc_input is registered once, and route_sources
    itself decides per-invocation whether it's active."""
    from discovery.graph import route_sources

    assert "process_adhoc_input" not in route_sources({"source_context": "daily"})
    assert "process_adhoc_input" in route_sources({"source_context": "saturday"})
