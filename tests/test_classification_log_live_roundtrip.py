"""
Real-store, real-Haiku live verification for sunday/nodes/classify_item.py's
classification_log writes (closeout-spec.md Section 4 point 1). Feeds two
throwaway correlated_items -- one clearly routine reading material
(expected plan_item), one clearly a structurally-new idea with no
matching Trello card (expected project_proposal) -- through the real
classify_item() node, then confirms both decisions landed as real,
separate classification_log entries. Deletes everything it wrote after
asserting.

Run: uv run --env-file .env python scripts/test_classification_log_live_roundtrip.py
"""
from dotenv import load_dotenv
load_dotenv()

from core.logging_config import setup_logging
setup_logging()

from sunday.nodes.classify_item import classify_item, _CLASSIFICATION_LOG_NAMESPACE
from sunday.memory_store_config import get_store

RUN_ID = "smoke-test-classification-log-live"

state = {
    "run_id": RUN_ID,
    "scored_items": [], "trello_cards": [], "pending_resumes": [],
    "correlated_items": [
        {
            "url": "https://example.com/smoke-classify-plan-item",
            "title": "A tutorial on LangGraph agent loops",
            "matched_card_id": None,
            "tags": ["agentic-engineering"],
            "reasoning": "a standard technical tutorial, nothing project-worthy",
        },
        {
            "url": "https://example.com/smoke-classify-proposal",
            "title": "We should build a real-time agent cost dashboard",
            "matched_card_id": None,
            "tags": ["evals"],
            "reasoning": "describes a structurally new tool/project idea, no existing tracked work covers this",
        },
    ],
    "classified_items": [], "plan_text": "", "plan_item_map": {}, "pending_approvals": [],
    "costs": [], "errors": [], "source_context": "sunday",
}

print("Calling the real classify_item() -- real Haiku classification call...")
result = classify_item(state)

print(f"\nclassified_items: {[(i['url'], i['classification'], i['proposal_type']) for i in result['classified_items']]}")
print(f"pending_approvals: {[i['url'] for i in result['pending_approvals']]}")
print(f"costs: {result['costs']}")

store = get_store()
entries = [
    obj.value for obj in store.search(_CLASSIFICATION_LOG_NAMESPACE, limit=200)
    if obj.value.get("run_id") == RUN_ID
]
print(f"\nreal classification_log entries for this run: {entries}")

assert len(entries) == 2, f"expected 2 real classification_log entries, got {len(entries)}"
by_item = {e["item_id"]: e for e in entries}
assert "https://example.com/smoke-classify-plan-item" in by_item
assert "https://example.com/smoke-classify-proposal" in by_item
decisions = {e["decision"] for e in entries}
print(f"\ndistinct real decisions logged this run: {decisions}")
assert "plan_item" in decisions, "expected at least one real plan_item decision logged"
# NOTE: the actual classification (plan_item vs project_proposal) is the real
# model's live judgment call, not asserted as a fixed outcome -- what's
# verified here is that whatever it decides gets logged for BOTH items,
# not just proposals.

# Cleanup
store_keys = [
    obj.key for obj in store.search(_CLASSIFICATION_LOG_NAMESPACE, limit=200)
    if obj.value.get("run_id") == RUN_ID
]
for key in store_keys:
    store.delete(_CLASSIFICATION_LOG_NAMESPACE, key)
print(f"\ndeleted {len(store_keys)} test classification_log entr(y/ies)")

print("\nclassification_log live round-trip: PASS")
print("  real classify_item() call logged a real classification_log entry for EVERY item, not just proposals")
print("  all test entries deleted after assertion")
