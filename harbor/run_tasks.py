from eval.evaluators import state_transition_ok
from telegram.delivery import split_html_message
from core.preferences import apply_confirmed_events


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


def main():
    assert state_transition_ok({"seen_urls": []}, {"seen_urls": []}, acknowledged=False)
    assert all(len(chunk) <= 3800 for chunk in split_html_message("x" * 9000))
    store = Store()
    event = {"event_id": "harbor-e1", "relevance": 3, "topics": ["evals"]}
    first = apply_confirmed_events(store, [event])
    second = apply_confirmed_events(store, [event])
    assert first["version"] == second["version"]
    print("Harbor recovery tasks passed")


if __name__ == "__main__":
    main()
