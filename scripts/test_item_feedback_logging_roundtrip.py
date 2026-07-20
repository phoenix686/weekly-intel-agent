"""
Real-store smoke test for sunday/approval_actions.py's handle_feedback,
same pattern as scripts/test_same_day_nudge_roundtrip.py: no embedding
dependency (handle_feedback no longer touches YAML/Haiku-rewrite/Gemini
at all -- that's the whole point of this checkpoint's change), so this
can be verified live right now. Writes real data under a throwaway
item_id to the live feedback_events AND same_day_adjustments namespaces
(the latter as a side effect, via the real apply_nudge call), verifies,
then deletes what it wrote from both.

Run: uv run --env-file .env python scripts/test_item_feedback_logging_roundtrip.py
"""
from dotenv import load_dotenv
load_dotenv()

from core.logging_config import setup_logging
setup_logging()

from sunday.approval_actions import handle_feedback, _FEEDBACK_NAMESPACE
from sunday.same_day_nudge import _NAMESPACE as _SAME_DAY_NAMESPACE, _week_key
from sunday.memory_store_config import get_store
from datetime import datetime, timezone

TEST_URL = "https://example.com/smoke-test-item-feedback-logging"
TEST_TAG = "smoke-test-item-feedback-logging-tag"

item = {"url": TEST_URL, "title": "Smoke Test Item", "text": "some real content body", "tags": [TEST_TAG]}

print("Calling handle_feedback() for real (real feedback_events write + real same_day_nudge Haiku call)...")
handle_feedback(item, feedback_text="loved this one, more like it please", sentiment="positive", run_id="smoke-test-run")

store = get_store()

matches = [
    obj for obj in store.search(_FEEDBACK_NAMESPACE, limit=200)
    if obj.value.get("item_id") == TEST_URL
]
print(f"feedback_events matches: {matches}")
assert len(matches) == 1, f"expected exactly 1 feedback_events entry, got {len(matches)}"
entry = matches[0].value
assert entry["feedback_text"] == "loved this one, more like it please"
assert entry["tags"] == [TEST_TAG]
assert entry["run_id"] == "smoke-test-run"
assert "replied_at" in entry

week_key = _week_key(datetime.now(timezone.utc))
same_day_key = f"{week_key}:{TEST_TAG}"
same_day_entry = store.get(_SAME_DAY_NAMESPACE, same_day_key)
print(f"same_day_adjustments side effect: {same_day_entry.value if same_day_entry else None}")
assert same_day_entry is not None, "expected the same-day nudge to have fired as a side effect"

# Confirm NO taste_profile.yaml touch happened as part of this -- the
# whole point of this checkpoint's change. Real file, real mtime check.
from pathlib import Path
profile_path = Path("data/taste_profile.yaml")
before_mtime = profile_path.stat().st_mtime if profile_path.exists() else None
handle_feedback(item, feedback_text="another reply, still no YAML touch", sentiment="positive", run_id="smoke-test-run-2")
after_mtime = profile_path.stat().st_mtime if profile_path.exists() else None
assert before_mtime == after_mtime, "taste_profile.yaml was touched -- regression, handle_feedback must not write it"

# Cleanup
store.delete(_FEEDBACK_NAMESPACE, matches[0].key)
for obj in store.search(_FEEDBACK_NAMESPACE, limit=200):
    if obj.value.get("item_id") == TEST_URL:
        store.delete(_FEEDBACK_NAMESPACE, obj.key)
store.delete(_SAME_DAY_NAMESPACE, same_day_key)

print("\nitem_feedback_logging round-trip: PASS")
print(f"  real feedback_events entry written and read back correctly")
print(f"  same_day_nudge fired as a real side effect (cumulative_adjustment={same_day_entry.value['cumulative_adjustment']})")
print(f"  taste_profile.yaml mtime unchanged across two real handle_feedback() calls (before={before_mtime}, after={after_mtime})")
print(f"  test entries deleted after assertion")
