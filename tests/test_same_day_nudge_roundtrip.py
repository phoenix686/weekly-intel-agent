"""
Real-store smoke test for sunday/same_day_nudge.py, same pattern as
scripts/test_pending_resume_map_roundtrip.py: no embedding dependency at
all (same_day_nudge only calls Haiku), so this has never needed an
embedding provider to verify. Writes real data
under a throwaway tag/item_id to the live same_day_adjustments namespace,
verifies, then deletes what it wrote.

Run: uv run --env-file .env python scripts/test_same_day_nudge_roundtrip.py
"""
from dotenv import load_dotenv
load_dotenv()

from core.logging_config import setup_logging
setup_logging()

from sunday.same_day_nudge import apply_nudge, _NAMESPACE, _week_key
from sunday.memory_store_config import get_store
from datetime import datetime, timezone

TEST_TAG = "smoke-test-same-day-nudge-tag"
week_key = _week_key(datetime.now(timezone.utc))
key = f"{week_key}:{TEST_TAG}"

print("Calling apply_nudge() with a real Haiku call against a clearly positive reply...")
costs = apply_nudge(
    "https://example.com/smoke-test-item",
    "this was a genuinely great find, exactly what I wanted more of",
    [TEST_TAG],
    "smoke-test-run",
)

print(f"costs: {costs}")
assert len(costs) == 1, f"expected 1 cost record for 1 tag, got {len(costs)}"
assert not costs[0].get("error"), f"unexpected error: {costs[0].get('error')}"
assert costs[0]["input_tokens"] > 0, "expected real token usage from a live Haiku call"

store = get_store()
stored = store.get(_NAMESPACE, key)
print(f"stored value: {stored.value if stored else None}")
assert stored is not None, "expected a real same_day_adjustments entry"
assert stored.value["tag"] == TEST_TAG
assert stored.value["cumulative_adjustment"] > 0, "expected a positive adjustment for clearly positive feedback"
assert "https://example.com/smoke-test-item" in stored.value["item_ids_contributing"]

store.delete(_NAMESPACE, key)
confirm_deleted = store.get(_NAMESPACE, key)
assert confirm_deleted is None, "cleanup failed -- test entry still present"

print(f"\nsame_day_nudge round-trip: PASS")
print(f"  direction inferred a positive cumulative_adjustment={stored.value['cumulative_adjustment']} from real Haiku classification")
print(f"  real tokens used: input={costs[0]['input_tokens']}, output={costs[0]['output_tokens']}, cost=${costs[0]['cost_usd']:.6f}")
print(f"  test key deleted after assertion")
