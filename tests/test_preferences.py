from core.preferences import apply_confirmed_events, apply_confirmed_events_locked


class Item:
    def __init__(self, value): self.value = value


class Store:
    def __init__(self): self.data = {}
    def get(self, ns, key):
        value = self.data.get((ns, key))
        return Item(value) if value else None
    def put(self, ns, key, value): self.data[(ns, key)] = dict(value)
    def batch(self, ops):
        for op in ops:
            self.put(op.namespace, op.key, op.value)


def test_preference_update_is_bounded_and_exactly_once():
    store = Store()
    event = {"event_id": "e1", "relevance": 3, "topics": ["security"],
             "liked_aspects": [], "new_interests": ["a2a"]}
    first = apply_confirmed_events(store, [event])
    second = apply_confirmed_events(store, [event])
    assert first["short_term"]["security"] == .15
    assert first["short_term"]["a2a"] == .4
    assert second["version"] == first["version"]


def test_preference_snapshot_and_event_marker_are_one_batch():
    store = Store()
    calls = []
    original = store.batch
    store.batch = lambda ops: (calls.append(list(ops)), original(ops))[1]
    apply_confirmed_events(store, [{
        "event_id": "e2", "relevance": 2, "topics": ["evals"],
        "liked_aspects": [], "new_interests": [],
    }])
    assert len(calls) == 1
    assert len(calls[0]) == 4  # current, immutable version, event, applied marker


def test_invalid_relevance_is_rejected_before_any_write():
    import pytest
    store = Store()
    with pytest.raises(ValueError):
        apply_confirmed_events(store, [{"event_id": "bad", "relevance": 7}])
    assert store.data == {}


def test_concurrent_confirmations_preserve_both_events():
    import threading
    store = Store()
    events = [
        {"event_id": f"concurrent-{index}", "relevance": 3, "topics": [f"topic-{index}"]}
        for index in range(2)
    ]
    threads = [
        threading.Thread(target=apply_confirmed_events_locked, args=(store, [event]))
        for event in events
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    snapshot = store.get(("weekly_intel", "preference_snapshots"), "current").value
    assert set(snapshot["provenance"]) == {"concurrent-0", "concurrent-1"}
