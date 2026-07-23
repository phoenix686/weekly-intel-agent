"""
Confirms the new village-namespace writes in assemble_digest.py and
assemble_plan.py round-trip correctly through the real PostgresStore:
namespace=("village",), key=f"event:{timestamp}".

Additive only -- does not touch or assert on the existing ("companion",)
writes, just the new store.put()/store.get() round trip for the
village-namespace event.

Run: uv run --env-file .env python tests/test_village_store_roundtrip.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

from daily.nodes.assemble_digest import assemble_digest
from sunday.nodes.assemble_plan import assemble_plan
from sunday.memory_store_config import get_store

VILLAGE_NAMESPACE = ("village",)
COMPANION_NAMESPACE = ("companion",)


def test_digest_ready_event() -> bool:
    scored_items = [{
        "url": "https://example.com/village-store-test",
        "title": "Village store round-trip test item",
        "text": "synthetic item for village store verification",
        "author_name": "test", "author_handle": "", "fetched_at": "2026-07-23T00:00:00+00:00",
        "is_thread": False, "thread_contents": None, "expanded_urls": [],
        "source": "test", "duplicate_count": 1,
        "keep": True, "reasoning": "test", "tags": ["test"],
    }]
    state = {"scored_items": scored_items, "uncategorized_items": [], "run_id": "village-store-test-daily"}

    before_keys = {item.key for item in get_store().search(VILLAGE_NAMESPACE)}
    assemble_digest(state)
    after_items = get_store().search(VILLAGE_NAMESPACE)
    new_items = [item for item in after_items if item.key not in before_keys]

    ok = (
        len(new_items) == 1
        and new_items[0].key.startswith("event:")
        and new_items[0].value.get("agent") == "weekly-intel"
        and new_items[0].value.get("event_type") == "digest_ready"
        and new_items[0].value.get("summary") == "1 item(s) kept"
        and "timestamp" in new_items[0].value
    )
    print(f"digest_ready event write: {'PASS' if ok else 'FAIL'}")
    if new_items:
        print(f"  key: {new_items[0].key}")
        print(f"  value: {new_items[0].value}")

    # Confirm this did NOT touch the existing companion-prefix write.
    companion_stored = get_store().get(COMPANION_NAMESPACE, "current_daily_digest")
    companion_ok = companion_stored is not None and companion_stored.value.get("run_id") == "village-store-test-daily"
    print(f"  companion prefix still writes correctly (untouched): {'PASS' if companion_ok else 'FAIL'}")

    return ok and companion_ok


def test_plan_ready_event() -> bool:
    classified_items = [{
        "url": "https://example.com/village-store-test-plan",
        "title": "Village store round-trip test plan item",
        "text": "synthetic item for village store verification",
        "reasoning": "test", "tags": ["test"], "matched_card_id": None,
        "classification": "plan_item",
    }]
    state = {
        "classified_items": classified_items,
        "uncategorized_items": [],
        "pending_approvals": [],
        "run_id": "village-store-test-sunday",
        "trello_cards": [],
        "prioritized_project_work": [],
    }

    before_keys = {item.key for item in get_store().search(VILLAGE_NAMESPACE)}
    assemble_plan(state)
    after_items = get_store().search(VILLAGE_NAMESPACE)
    new_items = [item for item in after_items if item.key not in before_keys]

    ok = (
        len(new_items) == 1
        and new_items[0].key.startswith("event:")
        and new_items[0].value.get("agent") == "weekly-intel"
        and new_items[0].value.get("event_type") == "plan_ready"
        and new_items[0].value.get("summary") == "1 plan item(s)"
        and "timestamp" in new_items[0].value
    )
    print(f"plan_ready event write: {'PASS' if ok else 'FAIL'}")
    if new_items:
        print(f"  key: {new_items[0].key}")
        print(f"  value: {new_items[0].value}")

    companion_stored = get_store().get(COMPANION_NAMESPACE, "current_weekly_plan")
    companion_ok = companion_stored is not None and companion_stored.value.get("run_id") == "village-store-test-sunday"
    print(f"  companion prefix still writes correctly (untouched): {'PASS' if companion_ok else 'FAIL'}")

    return ok and companion_ok


if __name__ == "__main__":
    digest_ok = test_digest_ready_event()
    plan_ok = test_plan_ready_event()
    print("\nVERDICT:", "PASS" if digest_ok and plan_ok else "FAIL")
