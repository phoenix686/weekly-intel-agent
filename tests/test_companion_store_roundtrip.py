"""
Confirms the companion-store additions to assemble_digest.py and
assemble_plan.py round-trip correctly through the real PostgresStore:
namespace=("companion",), key="current_daily_digest" / "current_weekly_plan".

Additive only -- does not touch or assert on existing Telegram/Trello
output behavior, just the new store.put()/store.get() round trip.

Run: uv run --env-file .env python scripts/test_companion_store_roundtrip.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

from daily.nodes.assemble_digest import assemble_digest
from saturday.nodes.assemble_plan import assemble_plan
from saturday.memory_store_config import get_store

COMPANION_NAMESPACE = ("companion",)


def test_daily_digest_roundtrip() -> bool:
    scored_items = [{
        "url": "https://example.com/companion-store-test",
        "title": "Companion store round-trip test item",
        "text": "synthetic item for companion store verification",
        "author_name": "test", "author_handle": "", "fetched_at": "2026-07-11T00:00:00+00:00",
        "is_thread": False, "thread_contents": None, "expanded_urls": [],
        "source": "test", "duplicate_count": 1,
        "keep": True, "reasoning": "test", "tags": ["test"],
    }]
    state = {"scored_items": scored_items, "uncategorized_items": [], "run_id": "companion-store-test-daily"}

    result = assemble_digest(state)
    assert "digest_text" in result and "digest_item_map" in result, "assemble_digest's existing return shape changed"

    stored = get_store().get(COMPANION_NAMESPACE, "current_daily_digest")
    ok = (
        stored is not None
        and stored.value.get("run_id") == "companion-store-test-daily"
        and stored.value.get("digest_text") == result["digest_text"]
        and "generated_at" in stored.value
    )
    print(f"daily digest round-trip: {'PASS' if ok else 'FAIL'}")
    if stored:
        print(f"  stored value: {stored.value}")
    return ok


def test_weekly_plan_roundtrip() -> bool:
    classified_items = [{
        "url": "https://example.com/companion-store-test-plan",
        "title": "Companion store round-trip test plan item",
        "text": "synthetic item for companion store verification",
        "reasoning": "test", "tags": ["test"], "matched_card_id": None,
        "classification": "plan_item",
    }]
    state = {
        "classified_items": classified_items,
        "uncategorized_items": [],
        "pending_approvals": [],
        "run_id": "companion-store-test-saturday",
        "trello_cards": [],
    }

    result = assemble_plan(state)
    assert "plan_text" in result and "plan_item_map" in result, "assemble_plan's existing return shape changed"

    stored = get_store().get(COMPANION_NAMESPACE, "current_weekly_plan")
    ok = (
        stored is not None
        and stored.value.get("run_id") == "companion-store-test-saturday"
        and stored.value.get("plan_text") == result["plan_text"]
        and "generated_at" in stored.value
    )
    print(f"weekly plan round-trip: {'PASS' if ok else 'FAIL'}")
    if stored:
        print(f"  stored value: {stored.value}")
    return ok


if __name__ == "__main__":
    daily_ok = test_daily_digest_roundtrip()
    plan_ok = test_weekly_plan_roundtrip()
    print("\nVERDICT:", "PASS" if daily_ok and plan_ok else "FAIL")
