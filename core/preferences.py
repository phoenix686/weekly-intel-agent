"""Versioned preference snapshots updated only from confirmed feedback."""
from __future__ import annotations
from datetime import datetime, timezone
import json
import os
import threading
from contextlib import contextmanager
from langgraph.store.base import PutOp
from core.model_gateway import estimate_tokens
from core.connection_pool import get_connection_pool

NAMESPACE = ("weekly_intel", "preference_snapshots")
CURRENT_KEY = "current"
APPLIED_NAMESPACE = ("weekly_intel", "applied_feedback_events")
CONFIRMED_NAMESPACE = ("weekly_intel", "confirmed_feedback_events")
SAME_DAY_NAMESPACE = ("weekly_intel", "same_day_adjustments")
_LOCAL_WRITE_LOCK = threading.RLock()


@contextmanager
def preference_write_lock():
    """Serialize snapshot read-modify-write across processes via Postgres."""
    with _LOCAL_WRITE_LOCK:
        if not os.getenv("DB_URI"):
            yield
            return
        with get_connection_pool().connection() as connection:
            connection.execute("SELECT pg_advisory_lock(hashtext('weekly-intel-preferences'))")
            try:
                yield
            finally:
                connection.execute("SELECT pg_advisory_unlock(hashtext('weekly-intel-preferences'))")


def default_snapshot() -> dict:
    return {
        "version": 1, "explicit": {}, "long_term": {}, "short_term": {},
        "positive_exemplars": [], "negative_exemplars": [], "new_interests": [],
        "exclusions": [], "provenance": [], "created_at": datetime.now(timezone.utc).isoformat(),
        "consolidated_event_ids": [],
    }


def load_snapshot(store) -> dict:
    if not hasattr(store, "get"):
        return default_snapshot()
    item = store.get(NAMESPACE, CURRENT_KEY)
    return dict(item.value) if item else default_snapshot()


def _current_week_key() -> str:
    iso = datetime.now(timezone.utc).isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


def load_effective_snapshot(store) -> dict:
    """Overlay current-week same-day nudges onto the persisted snapshot."""
    snapshot = load_snapshot(store)
    effective = dict(snapshot)
    effective["short_term"] = dict(snapshot.get("short_term", {}))
    if not hasattr(store, "search"):
        return effective

    current_week = _current_week_key()
    for item in store.search(SAME_DAY_NAMESPACE, limit=1000):
        value = item.value
        if value.get("week_of") != current_week:
            continue
        tag = value.get("tag")
        adjustment = value.get("cumulative_adjustment")
        if not isinstance(tag, str) or not isinstance(adjustment, (int, float)):
            continue
        old = effective["short_term"].get(tag, 0.0)
        effective["short_term"][tag] = round(max(-1.0, min(1.0, old + adjustment)), 4)
    return effective


def render_preference_context(snapshot: dict) -> str:
    explicit = snapshot.get("explicit", {})
    positive = sorted(snapshot.get("short_term", {}).items(), key=lambda item: item[1], reverse=True)
    negative = sorted(snapshot.get("short_term", {}).items(), key=lambda item: item[1])
    return (
        f"Preference snapshot version: {snapshot.get('version', 1)}\n"
        f"Explicit preferences (authoritative): {explicit}\n"
        f"Current positive signals: {positive[:12]}\n"
        f"Current negative signals: {negative[:8]}\n"
        f"Exclusions: {snapshot.get('exclusions', [])}\n"
        "Explicit preferences cannot be overridden by learned signals."
    )


def validate_feedback_event(event: dict) -> dict:
    normalized = dict(event)
    if not isinstance(normalized.get("event_id"), str) or not normalized["event_id"]:
        raise ValueError("feedback event requires a stable event_id")
    relevance = normalized.get("relevance", 1)
    if type(relevance) is not int or relevance not in {0, 1, 2, 3}:
        raise ValueError("feedback relevance must be an integer from 0 to 3")
    normalized["relevance"] = relevance
    for field in ("liked_aspects", "disliked_aspects", "topics", "new_interests", "digest_flags"):
        values = normalized.get(field, [])
        if not isinstance(values, list) or any(not isinstance(value, str) for value in values):
            raise ValueError(f"feedback {field} must be a list of strings")
        normalized[field] = values
    duplicate_of = normalized.get("duplicate_of")
    if duplicate_of is not None and type(duplicate_of) is not int:
        raise ValueError("duplicate_of must be an item number or null")
    normalized["confirmation_state"] = "confirmed"
    normalized["confirmed_at"] = datetime.now(timezone.utc).isoformat()
    return normalized


def apply_confirmed_events(store, events: list[dict]) -> dict:
    snapshot = load_snapshot(store)
    pending: list[dict] = []
    for raw_event in events:
        event = validate_feedback_event(raw_event)
        event_id = event["event_id"]
        if store.get(APPLIED_NAMESPACE, event_id):
            continue
        pending.append(event)
    if not pending:
        return snapshot

    for event in pending:
        event_id = event["event_id"]
        delta = {0: -1.0, 1: 0.0, 2: 0.5, 3: 1.0}[event["relevance"]]
        for aspect in event.get("liked_aspects", []) + event.get("topics", []):
            old = snapshot["short_term"].get(aspect, 0.0)
            snapshot["short_term"][aspect] = round(max(-1.0, min(1.0, old + 0.15 * delta)), 4)
        for aspect in event.get("disliked_aspects", []):
            old = snapshot["short_term"].get(aspect, 0.0)
            snapshot["short_term"][aspect] = round(max(-1.0, old - 0.15), 4)
        for interest in event.get("new_interests", []):
            snapshot["short_term"][interest] = max(0.4, snapshot["short_term"].get(interest, 0.0))
            if interest not in snapshot["new_interests"]:
                snapshot["new_interests"].append(interest)
        snapshot["provenance"].append(event_id)
    snapshot["version"] += 1
    snapshot["created_at"] = datetime.now(timezone.utc).isoformat()
    operations = [
        PutOp(NAMESPACE, CURRENT_KEY, dict(snapshot)),
        PutOp(NAMESPACE, f"v{snapshot['version']}", dict(snapshot)),
    ]
    for event in pending:
        operations.extend([
            PutOp(CONFIRMED_NAMESPACE, event["event_id"], event),
            PutOp(APPLIED_NAMESPACE, event["event_id"], {"applied_at": snapshot["created_at"]}),
        ])
    store.batch(operations)
    return snapshot


def apply_confirmed_events_locked(store, events: list[dict]) -> dict:
    with preference_write_lock():
        return apply_confirmed_events(store, events)


def consolidate_weekly(store, anthropic_client) -> tuple[dict, dict | None]:
    """Apply one validated patch when at least five events are unconsolidated."""
    snapshot = load_snapshot(store)
    consolidated = set(snapshot.get("consolidated_event_ids", []))
    events = [
        item.value for item in store.search(CONFIRMED_NAMESPACE, limit=500)
        if item.key not in consolidated
    ]
    if len(events) < 5:
        return snapshot, None
    events = sorted(events, key=lambda event: event.get("confirmed_at", ""))[-50:]
    def build_prompt(selected_events: list[dict]) -> str:
        return (
        "Propose a bounded preference patch from these confirmed feedback events. "
        "Do not rewrite the profile and do not modify explicit preferences. Return only JSON "
        'with {"long_term_adjustments":{"topic":number between -0.3 and 0.3},'
        '"exclusions_add":[],"new_interests_add":[]}.\n'
        f"Current snapshot: {json.dumps(snapshot)}\nEvents: {json.dumps(selected_events)}"
        )
    prompt = build_prompt(events)
    while len(events) > 5 and estimate_tokens(prompt) + 1024 > 7200:
        events = events[1:]
        prompt = build_prompt(events)
    if estimate_tokens(prompt) + 1024 > 7200:
        raise ValueError("weekly preference patch cannot fit the safe model envelope")
    response = anthropic_client.messages.create(
        model="claude-haiku-4-5", max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )
    patch = json.loads(response.content[0].text.strip())
    allowed = {"long_term_adjustments", "exclusions_add", "new_interests_add"}
    if set(patch) - allowed:
        raise ValueError("weekly preference patch contains unknown fields")
    adjustments = patch.get("long_term_adjustments", {})
    if not isinstance(adjustments, dict) or any(
        not isinstance(key, str) or not isinstance(value, (int, float)) or abs(value) > 0.3
        for key, value in adjustments.items()
    ):
        raise ValueError("weekly preference adjustments must be bounded to +/-0.3")
    updated = dict(snapshot)
    updated["long_term"] = dict(snapshot.get("long_term", {}))
    for key, delta in adjustments.items():
        updated["long_term"][key] = round(max(-1.0, min(1.0, updated["long_term"].get(key, 0) + delta)), 4)
    for field, patch_field in (("exclusions", "exclusions_add"), ("new_interests", "new_interests_add")):
        additions = patch.get(patch_field, [])
        if not isinstance(additions, list) or any(not isinstance(value, str) for value in additions):
            raise ValueError(f"{patch_field} must be a list of strings")
        updated[field] = list(dict.fromkeys(snapshot.get(field, []) + additions))
    updated["consolidated_event_ids"] = list(dict.fromkeys(
        snapshot.get("consolidated_event_ids", []) + [event["event_id"] for event in events]
    ))
    updated["version"] += 1
    updated["created_at"] = datetime.now(timezone.utc).isoformat()
    store.batch([
        PutOp(NAMESPACE, CURRENT_KEY, updated),
        PutOp(NAMESPACE, f"v{updated['version']}", updated),
    ])
    usage = {
        "input_tokens": response.usage.input_tokens,
        "output_tokens": response.usage.output_tokens,
    }
    return updated, usage


def consolidate_weekly_locked(store, anthropic_client) -> tuple[dict, dict | None]:
    with preference_write_lock():
        return consolidate_weekly(store, anthropic_client)
