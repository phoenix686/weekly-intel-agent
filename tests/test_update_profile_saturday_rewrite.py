from types import SimpleNamespace

from core.preferences import (
    CONFIRMED_NAMESPACE,
    consolidate_weekly,
    default_snapshot,
)


class Item:
    def __init__(self, key, value):
        self.key, self.value = key, value


class Store:
    def __init__(self, count):
        self.data = {}
        for index in range(count):
            event = {
                "event_id": f"e{index}", "relevance": 3, "topics": ["evals"],
                "liked_aspects": [], "new_interests": [], "digest_flags": [],
                "disliked_aspects": [], "confirmation_state": "confirmed",
            }
            self.data[(CONFIRMED_NAMESPACE, event["event_id"])] = event

    def get(self, namespace, key):
        value = self.data.get((namespace, key))
        return Item(key, value) if value else None

    def search(self, namespace, limit=500):
        return [Item(key, value) for (ns, key), value in self.data.items() if ns == namespace][:limit]

    def put(self, namespace, key, value):
        self.data[(namespace, key)] = dict(value)

    def batch(self, operations):
        for operation in operations:
            self.put(operation.namespace, operation.key, operation.value)


class Client:
    def __init__(self, patch):
        self.calls = 0
        self.patch = patch
        self.messages = self

    def create(self, **kwargs):
        import json
        self.calls += 1
        return SimpleNamespace(
            content=[SimpleNamespace(text=json.dumps(self.patch))],
            usage=SimpleNamespace(input_tokens=100, output_tokens=20),
        )


def test_fewer_than_five_confirmed_events_skips_weekly_call():
    client = Client({})
    snapshot, usage = consolidate_weekly(Store(4), client)
    assert snapshot["version"] == default_snapshot()["version"]
    assert usage is None
    assert client.calls == 0


def test_five_events_produce_one_bounded_patch_and_preserve_explicit_preferences():
    store = Store(5)
    snapshot = default_snapshot()
    snapshot["explicit"] = {"never_drop": ["security"]}
    store.put(("weekly_intel", "preference_snapshots"), "current", snapshot)
    client = Client({
        "long_term_adjustments": {"evals": 0.2},
        "exclusions_add": ["job-posts"],
        "new_interests_add": ["a2a"],
    })
    updated, usage = consolidate_weekly(store, client)
    assert client.calls == 1
    assert usage == {"input_tokens": 100, "output_tokens": 20}
    assert updated["explicit"] == snapshot["explicit"]
    assert updated["long_term"]["evals"] == 0.2
    assert len(updated["consolidated_event_ids"]) == 5


def test_weekly_patch_rejects_out_of_bounds_adjustment():
    import pytest
    with pytest.raises(ValueError):
        consolidate_weekly(Store(5), Client({
            "long_term_adjustments": {"evals": 0.8},
            "exclusions_add": [], "new_interests_add": [],
        }))
