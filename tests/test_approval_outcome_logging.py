"""
approval-outcome-logging (Checkpoint 4, closeout-spec.md Section 4 point 2):
saturday/approval_actions.py logs BOTH approved and rejected proposal
outcomes to the store under namespace=("weekly_intel","approval_log") --
previously only rejections were recorded (rejection_event, since
superseded/renamed feedback_events).
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unittest.mock import patch, MagicMock

import saturday.approval_actions as approval_actions


class _FakeStore:
    def __init__(self, raise_on_put=False):
        self.puts: list[tuple] = []
        self._raise_on_put = raise_on_put

    def put(self, namespace, key, value):
        if self._raise_on_put:
            raise RuntimeError("simulated store failure")
        self.puts.append((namespace, key, value))

    def get(self, namespace, key):
        return None


def test_handle_approval_logs_approved_outcome():
    fake_store = _FakeStore()
    item = {"url": "https://example.com/approved-item", "title": "New idea", "text": "content", "reasoning": "worth doing", "proposal_type": "new"}

    with patch.object(approval_actions, "get_store", return_value=fake_store), \
         patch.object(approval_actions, "create_trello_card", return_value={"name": "New idea", "url": "https://trello.com/c/x"}), \
         patch.object(approval_actions, "get_dump_list_id", return_value="list-1"), \
         patch.object(approval_actions, "send_message"):
        approval_actions.handle_approval(item, thread_id="thread-1", run_id="run-approved-1")

    log_puts = [p for p in fake_store.puts if p[0] == approval_actions._APPROVAL_LOG_NAMESPACE]
    assert len(log_puts) == 1
    _, _, value = log_puts[0]
    assert value == {"item_id": "https://example.com/approved-item", "outcome": "approved", "run_id": "run-approved-1"}


def test_handle_rejection_logs_rejected_outcome():
    fake_store = _FakeStore()
    item = {"url": "https://example.com/rejected-item", "title": "T", "text": "content", "tags": []}

    with patch.object(approval_actions, "get_store", return_value=fake_store), \
         patch.object(approval_actions, "apply_nudge", return_value=[]):
        approval_actions.handle_rejection(item, run_id="run-rejected-1")

    log_puts = [p for p in fake_store.puts if p[0] == approval_actions._APPROVAL_LOG_NAMESPACE]
    assert len(log_puts) == 1
    _, _, value = log_puts[0]
    assert value == {"item_id": "https://example.com/rejected-item", "outcome": "rejected", "run_id": "run-rejected-1"}

    # Rejection still goes through the existing feedback_events path too -- unchanged
    feedback_puts = [p for p in fake_store.puts if p[0] == approval_actions._FEEDBACK_NAMESPACE]
    assert len(feedback_puts) == 1
    assert feedback_puts[0][2]["sentiment"] == "negative"


def test_one_approved_and_one_rejected_both_produce_distinct_approval_log_entries():
    """Mirrors the feature's own verification requirement: one approved
    and one rejected proposal in the same run both land in approval_log
    with the correct outcome value."""
    fake_store = _FakeStore()
    approved_item = {"url": "https://example.com/a", "title": "A", "text": "c", "reasoning": "r", "proposal_type": "new"}
    rejected_item = {"url": "https://example.com/b", "title": "B", "text": "c", "tags": []}

    with patch.object(approval_actions, "get_store", return_value=fake_store), \
         patch.object(approval_actions, "create_trello_card", return_value={"name": "A", "url": "https://trello.com/c/a"}), \
         patch.object(approval_actions, "get_dump_list_id", return_value="list-1"), \
         patch.object(approval_actions, "send_message"), \
         patch.object(approval_actions, "apply_nudge", return_value=[]):
        approval_actions.handle_approval(approved_item, thread_id="thread-a", run_id="run-both-1")
        approval_actions.handle_rejection(rejected_item, run_id="run-both-1")

    log_puts = [p[2] for p in fake_store.puts if p[0] == approval_actions._APPROVAL_LOG_NAMESPACE]
    assert len(log_puts) == 2
    by_item = {e["item_id"]: e for e in log_puts}
    assert by_item["https://example.com/a"]["outcome"] == "approved"
    assert by_item["https://example.com/b"]["outcome"] == "rejected"


def test_failed_approval_log_write_does_not_block_real_approval_action():
    fake_store = _FakeStore(raise_on_put=True)
    item = {"url": "https://example.com/x", "title": "T", "text": "content", "reasoning": "r", "proposal_type": "new"}

    with patch.object(approval_actions, "get_store", return_value=fake_store), \
         patch.object(approval_actions, "create_trello_card", return_value={"name": "T", "url": "https://trello.com/c/x"}) as mock_create, \
         patch.object(approval_actions, "get_dump_list_id", return_value="list-1"), \
         patch.object(approval_actions, "send_message") as mock_send:
        approval_actions.handle_approval(item, thread_id="thread-1", run_id="run-1")  # must not raise

    mock_create.assert_called_once()
    mock_send.assert_called_once()
