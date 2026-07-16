"""
item-feedback-logging (Checkpoint 4), rebuilt per batch2-dedup-taste-spec.md
Section 7 item 1: on a reply, approval_actions.handle_feedback logs a
discrete feedback_events record and stops -- no Haiku rewrite call, no
taste_profile.yaml write. This replaces the prior uncapped, immediate,
full-profile Haiku rewrite that was previously running on every reply
(Section 0's investigation finding) -- the regression check below
confirms that behavior is actually gone, not just that logging was
added alongside it.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unittest.mock import patch, MagicMock

import sunday.approval_actions as approval_actions


class _Item:
    def __init__(self, key, value):
        self.key = key
        self.value = value


class _FakeStore:
    def __init__(self):
        self.puts: list[tuple] = []

    def put(self, namespace, key, value):
        self.puts.append((namespace, key, value))

    def get(self, namespace, key):
        return None


def test_handle_feedback_writes_one_feedback_events_record(tmp_path):
    fake_store = _FakeStore()
    item = {"url": "https://example.com/x", "title": "Some Title", "text": "some content" * 10, "tags": ["agentic-engineering"]}

    with patch.object(approval_actions, "get_store", return_value=fake_store), \
         patch.object(approval_actions, "apply_nudge", return_value=[]) as mock_nudge:
        approval_actions.handle_feedback(item, feedback_text="loved it", sentiment="positive", run_id="run-1")

    feedback_puts = [p for p in fake_store.puts if p[0] == approval_actions._FEEDBACK_NAMESPACE]
    assert len(feedback_puts) == 1
    _, _, value = feedback_puts[0]
    assert value["item_id"] == "https://example.com/x"
    assert value["feedback_text"] == "loved it"
    assert value["run_id"] == "run-1"
    assert "replied_at" in value
    assert value["tags"] == ["agentic-engineering"]
    mock_nudge.assert_called_once_with("https://example.com/x", "loved it", ["agentic-engineering"], "run-1")


def test_handle_feedback_makes_no_anthropic_call_and_no_yaml_write(tmp_path):
    """REGRESSION CHECK: the old behavior (full Haiku rewrite of
    taste_profile.yaml on every reply) must actually be gone, not just
    supplemented by the new logging."""
    fake_store = _FakeStore()
    item = {"url": "https://example.com/x", "title": "T", "text": "content", "tags": []}
    fake_yaml_path = tmp_path / "taste_profile.yaml"
    fake_yaml_path.write_text("version: 1\nproposal_filters: []\nnotes: ''", encoding="utf-8")
    before = fake_yaml_path.read_text(encoding="utf-8")

    with patch.object(approval_actions, "get_store", return_value=fake_store), \
         patch.object(approval_actions, "apply_nudge", return_value=[]):
        approval_actions.handle_feedback(item, feedback_text="meh", sentiment="negative", run_id="run-2")

    # File untouched -- approval_actions.py doesn't even reference this path anymore
    assert fake_yaml_path.read_text(encoding="utf-8") == before
    assert not hasattr(approval_actions, "TASTE_PROFILE_PATH")
    assert not hasattr(approval_actions, "_update_yaml_for_feedback")
    assert not hasattr(approval_actions, "_client")
    assert "anthropic" not in dir(approval_actions)


def test_handle_rejection_is_negative_feedback_through_the_same_path():
    fake_store = _FakeStore()
    item = {"url": "https://example.com/rejected", "title": "T", "text": "content", "tags": ["evals"]}

    with patch.object(approval_actions, "get_store", return_value=fake_store), \
         patch.object(approval_actions, "apply_nudge", return_value=[]) as mock_nudge:
        approval_actions.handle_rejection(item, run_id="run-3")

    feedback_puts = [p for p in fake_store.puts if p[0] == approval_actions._FEEDBACK_NAMESPACE]
    assert len(feedback_puts) == 1
    assert feedback_puts[0][2]["sentiment"] == "negative"
    assert feedback_puts[0][2]["feedback_text"] == "rejected proposal"
    mock_nudge.assert_called_once()
