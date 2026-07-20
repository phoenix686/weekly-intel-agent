"""
Real-store live verification for sunday/approval_actions.py's approval_log
writes (closeout-spec.md Section 4 point 2). Same pattern as
scripts/test_pending_resume_map_roundtrip.py: mocks only the external,
visible-side-effect calls (Trello card creation, Telegram send -- creating
a real Trello card or sending a real Telegram message is not something a
smoke test should do unprompted), and hits the REAL Supabase Postgres
store for the actual thing this feature verifies. Writes real
approval_log entries for one approved and one rejected proposal, verifies
both, then deletes everything it wrote.

Run: uv run --env-file .env python scripts/test_approval_outcome_log_live_roundtrip.py
"""
from dotenv import load_dotenv
load_dotenv()

from core.logging_config import setup_logging
setup_logging()

from unittest.mock import patch

import sunday.approval_actions as approval_actions
from sunday.memory_store_config import get_store

RUN_ID = "smoke-test-approval-outcome-log-live"

approved_item = {
    "url": "https://example.com/smoke-approval-log-approved", "title": "Smoke Approved Item",
    "text": "some real content", "reasoning": "worth doing", "proposal_type": "new",
}
rejected_item = {
    "url": "https://example.com/smoke-approval-log-rejected", "title": "Smoke Rejected Item",
    "text": "some real content", "tags": [],
}

print("Calling the real handle_approval() (Trello/Telegram mocked, store real)...")
with patch.object(approval_actions, "create_trello_card", return_value={"name": "Smoke Approved Item", "url": "https://trello.com/c/fake"}), \
     patch.object(approval_actions, "get_dump_list_id", return_value="fake-list-id"), \
     patch.object(approval_actions, "send_message"):
    approval_actions.handle_approval(approved_item, thread_id="smoke-thread-approve", run_id=RUN_ID)

print("Calling the real handle_rejection() (same_day_nudge Haiku call is real, store real)...")
approval_actions.handle_rejection(rejected_item, run_id=RUN_ID)

store = get_store()
entries = [
    obj.value for obj in store.search(approval_actions._APPROVAL_LOG_NAMESPACE, limit=200)
    if obj.value.get("run_id") == RUN_ID
]
print(f"\nreal approval_log entries for this run: {entries}")

assert len(entries) == 2, f"expected 2 real approval_log entries, got {len(entries)}"
by_item = {e["item_id"]: e for e in entries}
assert by_item["https://example.com/smoke-approval-log-approved"]["outcome"] == "approved"
assert by_item["https://example.com/smoke-approval-log-rejected"]["outcome"] == "rejected"
print("\nBoth outcomes confirmed: one 'approved', one 'rejected', distinct real entries.")

# Cleanup
keys = [
    obj.key for obj in store.search(approval_actions._APPROVAL_LOG_NAMESPACE, limit=200)
    if obj.value.get("run_id") == RUN_ID
]
for key in keys:
    store.delete(approval_actions._APPROVAL_LOG_NAMESPACE, key)
# handle_rejection also wrote a real feedback_events entry via handle_feedback -- clean that up too.
feedback_keys = [
    obj.key for obj in store.search(approval_actions._FEEDBACK_NAMESPACE, limit=200)
    if obj.value.get("run_id") == RUN_ID
]
for key in feedback_keys:
    store.delete(approval_actions._FEEDBACK_NAMESPACE, key)

print(f"\ndeleted {len(keys)} approval_log + {len(feedback_keys)} feedback_events test entr(y/ies)")
print("\napproval_outcome_log live round-trip: PASS")
