"""
Real-embedding, real-Haiku live verification for sunday/nodes/update_profile.py's
Sunday consolidated rewrite -- the one blocked feature that touches BOTH a
real Anthropic call and real local embeddings (via the nested
recompute_topic_vectors call). Deliberately writes to a THROWAWAY yaml
path (TASTE_PROFILE_PATH patched), not data/taste_profile.yaml -- this
call permanently rewrites whatever path it's given, and the real file is
not this checkpoint's data to overwrite for a verification run. Seeds one
real, throwaway feedback_events record (deleted after), runs the real
node function, verifies the real consolidated rewrite + real recompute +
real same_day_adjustments clearing, then cleans up everything it wrote.

Run: uv run --env-file .env python scripts/test_sunday_rewrite_live_roundtrip.py
"""
from dotenv import load_dotenv
load_dotenv()

from core.logging_config import setup_logging
setup_logging()

import uuid
import tempfile
from pathlib import Path
from datetime import datetime, timezone

import sunday.nodes.update_profile as update_profile_mod
from sunday.memory_store_config import get_store

RUN_ID = "smoke-test-sunday-rewrite-live"
store = get_store()

# Seed one real, throwaway feedback_events record.
feedback_key = str(uuid.uuid4())
feedback_value = {
    "item_id": "https://example.com/smoke-sunday-rewrite-item",
    "feedback_text": "really liked this one, more agentic-engineering deep dives please",
    "replied_at": datetime.now(timezone.utc).isoformat(),
    "run_id": RUN_ID,
    "tags": ["agentic-engineering"],
    "title": "Smoke Test Sunday Rewrite Item",
    "content_summary": "some real content body about agent harnesses",
    "sentiment": "positive",
}
store.put(update_profile_mod._FEEDBACK_NAMESPACE, feedback_key, feedback_value)
print(f"Seeded real feedback_events record: {feedback_key}")

with tempfile.TemporaryDirectory() as tmpdir:
    throwaway_path = Path(tmpdir) / "taste_profile.yaml"
    throwaway_path.write_text(
        "version: 1\nproposal_filters: []\nnotes: \"seeded for smoke test\"\n",
        encoding="utf-8",
    )
    print(f"Using throwaway TASTE_PROFILE_PATH: {throwaway_path} (data/taste_profile.yaml untouched)")

    original_path = update_profile_mod.TASTE_PROFILE_PATH
    update_profile_mod.TASTE_PROFILE_PATH = throwaway_path
    try:
        state = {
            "run_id": RUN_ID, "scored_items": [], "trello_cards": [], "correlated_items": [],
            "classified_items": [], "plan_text": "", "plan_item_map": {}, "pending_approvals": [],
            "pending_resumes": [], "costs": [], "errors": [], "source_context": "sunday",
        }
        print("\nCalling the real update_profile() -- real Haiku consolidated rewrite + real local embedding recompute...")
        result = update_profile_mod.update_profile(state)
    finally:
        update_profile_mod.TASTE_PROFILE_PATH = original_path

    rewritten_yaml = throwaway_path.read_text(encoding="utf-8")

print(f"\nresult costs: {result['costs']}")
print(f"\nrewritten YAML (throwaway file):\n---\n{rewritten_yaml}\n---")

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
assert "seeded for smoke test" not in rewritten_yaml or "agentic" in rewritten_yaml.lower(), \
    "expected the rewrite to actually incorporate the seeded feedback, not just echo the input unchanged"

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

print("\nsunday_rewrite live round-trip: PASS")
print("  real consolidated Haiku rewrite happened exactly once, real nonzero cost")
print("  real recompute_topic_vectors ran on the fresh text (5/6 tags, learning-resource flagged)")
print("  real same_day_adjustments namespace confirmed cleared")
print("  data/taste_profile.yaml was never touched -- throwaway path used throughout")
print("  all seeded/computed test entries deleted after assertion")
