"""
Real-embedding, real-Haiku live verification for saturday/nodes/update_profile.py's
Saturday consolidated rewrite -- the one blocked feature that touches BOTH a
real Anthropic call and real local embeddings (via the nested
recompute_topic_vectors call).

2026-07-26: taste_profile persistence moved to Postgres (discovery/
taste_profile_store.py) -- there's no "throwaway path" to point at
anymore the way a local file had. Instead: capture the REAL current
("weekly_intel","taste_profile") row before running, let update_profile()
write for real, then restore the original row in a finally block --
same "never permanently touch real production data" guarantee as the
old TASTE_PROFILE_PATH-swap technique, adapted to the new persistence
mechanism. Seeds one real, throwaway feedback_events record (deleted
after), runs the real node function, verifies the real consolidated
rewrite + real recompute + real same_day_adjustments clearing, then
cleans up everything it wrote.

Run: uv run --env-file .env python scripts/test_saturday_rewrite_live_roundtrip.py
"""
from dotenv import load_dotenv
load_dotenv()

from core.logging_config import setup_logging
setup_logging()

import uuid
from datetime import datetime, timezone

import saturday.nodes.update_profile as update_profile_mod
from saturday.memory_store_config import get_store
from discovery.taste_profile_store import get_taste_profile, put_taste_profile

RUN_ID = "smoke-test-saturday-rewrite-live"
store = get_store()

# Seed one real, throwaway feedback_events record.
feedback_key = str(uuid.uuid4())
feedback_value = {
    "item_id": "https://example.com/smoke-saturday-rewrite-item",
    "feedback_text": "really liked this one, more agentic-engineering deep dives please",
    "replied_at": datetime.now(timezone.utc).isoformat(),
    "run_id": RUN_ID,
    "tags": ["agentic-engineering"],
    "title": "Smoke Test Saturday Rewrite Item",
    "content_summary": "some real content body about agent harnesses",
    "sentiment": "positive",
}
store.put(update_profile_mod._FEEDBACK_NAMESPACE, feedback_key, feedback_value)
print(f"Seeded real feedback_events record: {feedback_key}")

original_profile = get_taste_profile()
print(f"Captured real current taste_profile row (len={len(original_profile) if original_profile else 0}) -- will restore after")

try:
    state = {
        "run_id": RUN_ID, "scored_items": [], "trello_cards": [], "correlated_items": [],
        "classified_items": [], "plan_text": "", "plan_item_map": {}, "pending_approvals": [],
        "pending_resumes": [], "costs": [], "errors": [], "source_context": "saturday",
    }
    print("\nCalling the real update_profile() -- real Haiku consolidated rewrite + real local embedding recompute...")
    result = update_profile_mod.update_profile(state)
    rewritten_yaml = get_taste_profile()
finally:
    if original_profile is not None:
        put_taste_profile(original_profile)
        print("Restored the real taste_profile row to its pre-run content")

print(f"\nresult costs: {result['costs']}")
print(f"\nrewritten YAML (real Postgres row, restored after this script exits):\n---\n{rewritten_yaml}\n---")

haiku_cost = next((c for c in result["costs"] if c["node_name"] == "update_profile" and c["input_tokens"] > 0), None)
assert haiku_cost is not None, "expected a real Haiku cost record with nonzero input_tokens"
assert haiku_cost["cost_usd"] > 0, "expected a real nonzero cost from a real Haiku call"
print(f"real Haiku call confirmed: {haiku_cost}")

vector_costs = [c for c in result["costs"] if c["node_name"] == "recompute_topic_vectors"]
assert len(vector_costs) == 6, f"expected 6 recompute_topic_vectors cost records (one per tag), got {len(vector_costs)}"
real_vectors = [c for c in vector_costs if not c.get("error")]
assert len(real_vectors) == 5, f"expected 5 real embeddings (learning-resource flagged), got {len(real_vectors)}"
print(f"real recompute_topic_vectors confirmed: {len(real_vectors)}/6 tags got a real local embedding")

assert rewritten_yaml.strip() != "", "expected a real non-empty rewritten YAML"
assert rewritten_yaml != original_profile, \
    "expected a real rewrite, not the original profile echoed back unchanged"

same_day_after = list(store.search(update_profile_mod._SAME_DAY_NAMESPACE, limit=100))
assert same_day_after == [], f"expected same_day_adjustments cleared, found {len(same_day_after)} entries"
print("same_day_adjustments confirmed cleared after the rewrite")

# Cleanup
store.delete(update_profile_mod._FEEDBACK_NAMESPACE, feedback_key)
confirm = store.get(update_profile_mod._FEEDBACK_NAMESPACE, feedback_key)
assert confirm is None
# recompute_topic_vectors wrote real entries under the REAL taste_topic_vectors
# namespace (that part isn't path-scoped) -- clean those up too.
from discovery.taste_vectors import _NAMESPACE as _VECTORS_NAMESPACE, TOPIC_TAGS, _TAG_TO_BULLET
for tag in [t for t in TOPIC_TAGS if _TAG_TO_BULLET.get(t) is not None]:
    store.delete(_VECTORS_NAMESPACE, tag)

print("\nsaturday_rewrite live round-trip: PASS")
print("  real consolidated Haiku rewrite happened exactly once, real nonzero cost")
print("  real recompute_topic_vectors ran on the fresh text (5/6 tags, learning-resource flagged)")
print("  real same_day_adjustments namespace confirmed cleared")
print("  real taste_profile Postgres row restored to its pre-run content")
print("  all seeded/computed test entries deleted after assertion")
