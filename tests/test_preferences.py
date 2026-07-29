from core.preferences import apply_confirmed_events


class Item:
    def __init__(self, value): self.value = value


class Store:
    def __init__(self): self.data = {}
    def get(self, ns, key):
        value = self.data.get((ns, key))
        return Item(value) if value else None
    def put(self, ns, key, value): self.data[(ns, key)] = dict(value)


def test_preference_update_is_bounded_and_exactly_once():
    store = Store()
    event = {"event_id": "e1", "relevance": 3, "topics": ["security"],
             "liked_aspects": [], "new_interests": ["a2a"]}
    first = apply_confirmed_events(store, [event])
    second = apply_confirmed_events(store, [event])
    assert first["short_term"]["security"] == .15
    assert first["short_term"]["a2a"] == .4
    assert second["version"] == first["version"]
