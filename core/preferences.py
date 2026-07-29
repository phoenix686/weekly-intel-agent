"""Versioned preference snapshots updated only from confirmed feedback."""
from __future__ import annotations
from datetime import datetime, timezone

NAMESPACE = ("weekly_intel", "preference_snapshots")
CURRENT_KEY = "current"
APPLIED_NAMESPACE = ("weekly_intel", "applied_feedback_events")


def default_snapshot() -> dict:
    return {
        "version": 1, "explicit": {}, "long_term": {}, "short_term": {},
        "positive_exemplars": [], "negative_exemplars": [], "new_interests": [],
        "exclusions": [], "provenance": [], "created_at": datetime.now(timezone.utc).isoformat(),
    }


def load_snapshot(store) -> dict:
    item = store.get(NAMESPACE, CURRENT_KEY)
    return dict(item.value) if item else default_snapshot()


def apply_confirmed_events(store, events: list[dict]) -> dict:
    snapshot = load_snapshot(store)
    changed = False
    for event in events:
        event_id = event["event_id"]
        if store.get(APPLIED_NAMESPACE, event_id):
            continue
        delta = {0: -1.0, 1: 0.0, 2: 0.5, 3: 1.0}[event["relevance"]]
        for aspect in event.get("liked_aspects", []) + event.get("topics", []):
            old = snapshot["short_term"].get(aspect, 0.0)
            snapshot["short_term"][aspect] = round(max(-1.0, min(1.0, old + 0.15 * delta)), 4)
        for interest in event.get("new_interests", []):
            snapshot["short_term"][interest] = max(0.4, snapshot["short_term"].get(interest, 0.0))
            if interest not in snapshot["new_interests"]:
                snapshot["new_interests"].append(interest)
        store.put(APPLIED_NAMESPACE, event_id, {"applied_at": datetime.now(timezone.utc).isoformat()})
        snapshot["provenance"].append(event_id)
        changed = True
    if changed:
        snapshot["version"] += 1
        snapshot["created_at"] = datetime.now(timezone.utc).isoformat()
        store.put(NAMESPACE, CURRENT_KEY, snapshot)
        store.put(NAMESPACE, f"v{snapshot['version']}", snapshot)
    return snapshot
