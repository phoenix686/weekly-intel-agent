import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unittest.mock import patch, MagicMock

from langgraph.types import Command

import telegram.polling as polling


class _Item:
    def __init__(self, value):
        self.value = value


class _FakeStore:
    """In-memory stand-in for PostgresStore, keyed by (namespace, key)."""

    def __init__(self, seed: dict | None = None):
        self._data = dict(seed or {})
        self.deleted: list[tuple] = []
        self.put_calls: list[tuple] = []

    def get(self, namespace, key):
        value = self._data.get((namespace, key))
        return _Item(value) if value is not None else None

    def put(self, namespace, key, value):
        self._data[(namespace, key)] = value
        self.put_calls.append((namespace, key, value))

    def delete(self, namespace, key):
        self._data.pop((namespace, key), None)
        self.deleted.append((namespace, key))


def _resume_update(update_id, reply_msg_id, text="approve"):
    return {
        "update_id": update_id,
        "message": {
            "message_id": update_id * 10,
            "text": text,
            "reply_to_message": {"message_id": reply_msg_id},
        },
    }


def _feedback_update(update_id, reply_msg_id, text="loved it"):
    return {
        "update_id": update_id,
        "message": {
            "message_id": update_id * 10,
            "text": text,
            "reply_to_message": {"message_id": reply_msg_id},
        },
    }


def _adhoc_update(update_id, text="check this out"):
    return {
        "update_id": update_id,
        "message": {
            "message_id": update_id * 10,
            "text": text,
        },
    }


def test_three_mocked_updates_each_route_to_the_correct_handler():
    """One reply matching pending_resume_map (-> resume), one reply matching
    a digest/plan message i.e. no resume-map match (-> feedback), and one
    with no reply_to_message at all (-> ad-hoc queue)."""
    resume_msg_id = 501
    feedback_reply_id = 777  # not present in either resume map -> falls through to feedback

    fake_store = _FakeStore(seed={
        (("weekly_intel", "pending_resume_map"), str(resume_msg_id)): {
            "thread_id": "proposal-abc123",
            "proposal_id": "https://example.com/p",
            "run_id": "run-1",
        },
    })

    updates = [
        _resume_update(1, resume_msg_id, text="approve"),
        _feedback_update(2, feedback_reply_id, text="great pick"),
        _adhoc_update(3, text="new ad-hoc idea"),
    ]

    fake_child = MagicMock()
    fake_child.invoke.return_value = {"url": "https://example.com/p"}

    with patch("telegram.polling.get_store", return_value=fake_store), \
         patch("telegram.polling._get_updates", return_value=updates), \
         patch("telegram.polling.get_proposal_graph", return_value=fake_child), \
         patch("telegram.polling.handle_approval") as mock_approval, \
         patch("telegram.polling.handle_rejection") as mock_rejection, \
         patch.object(polling.feedback_router, "handle_feedback") as mock_feedback:
        polling.poll_once()

    # 1. resume path: Command(resume="approve") invoked against the right thread_id
    fake_child.invoke.assert_called_once()
    call_args, call_kwargs = fake_child.invoke.call_args
    resume_command = call_args[0]
    assert isinstance(resume_command, Command)
    assert resume_command.resume == "approve"
    assert call_kwargs["config"]["configurable"]["thread_id"] == "proposal-abc123"
    mock_approval.assert_called_once()
    mock_rejection.assert_not_called()
    # pending_resume_map entry deleted after successful resume
    assert (("weekly_intel", "pending_resume_map"), str(resume_msg_id)) in fake_store.deleted

    # 2. feedback path: routed to feedback_router.handle_feedback with the raw message
    mock_feedback.assert_called_once()
    (routed_message,), _ = mock_feedback.call_args
    assert routed_message["text"] == "great pick"

    # 3. ad-hoc path: queued to the store under adhoc_queue
    adhoc_puts = [c for c in fake_store.put_calls if c[0] == ("weekly_intel", "adhoc_queue")]
    assert len(adhoc_puts) == 1
    assert adhoc_puts[0][2]["text"] == "new ad-hoc idea"


def test_resume_reply_with_unrecognized_decision_does_not_resume_or_delete():
    resume_msg_id = 501
    fake_store = _FakeStore(seed={
        (("weekly_intel", "pending_resume_map"), str(resume_msg_id)): {
            "thread_id": "proposal-abc123",
            "proposal_id": "https://example.com/p",
            "run_id": "run-1",
        },
    })
    updates = [_resume_update(1, resume_msg_id, text="huh?")]
    fake_child = MagicMock()

    with patch("telegram.polling.get_store", return_value=fake_store), \
         patch("telegram.polling._get_updates", return_value=updates), \
         patch("telegram.polling.get_proposal_graph", return_value=fake_child), \
         patch("telegram.bot_client.send_message") as mock_send:
        polling.poll_once()

    fake_child.invoke.assert_not_called()
    assert fake_store.deleted == []
    mock_send.assert_called_once()


def test_reject_reply_calls_handle_rejection_not_approval():
    resume_msg_id = 501
    fake_store = _FakeStore(seed={
        (("weekly_intel", "pending_resume_map"), str(resume_msg_id)): {
            "thread_id": "proposal-abc123",
            "proposal_id": "https://example.com/p",
            "run_id": "run-1",
        },
    })
    updates = [_resume_update(1, resume_msg_id, text="reject")]
    fake_child = MagicMock()
    fake_child.invoke.return_value = {"url": "https://example.com/p"}

    with patch("telegram.polling.get_store", return_value=fake_store), \
         patch("telegram.polling._get_updates", return_value=updates), \
         patch("telegram.polling.get_proposal_graph", return_value=fake_child), \
         patch("telegram.polling.handle_approval") as mock_approval, \
         patch("telegram.polling.handle_rejection") as mock_rejection:
        polling.poll_once()

    call_args, _ = fake_child.invoke.call_args
    assert call_args[0].resume == "reject"
    mock_rejection.assert_called_once()
    mock_approval.assert_not_called()


def test_poll_once_advances_offset_past_last_update():
    fake_store = _FakeStore()
    updates = [_adhoc_update(42, text="x")]

    with patch("telegram.polling.get_store", return_value=fake_store), \
         patch("telegram.polling._get_updates", return_value=updates):
        polling.poll_once()

    offset_put = [c for c in fake_store.put_calls if c[0] == polling._OFFSET_NAMESPACE]
    assert len(offset_put) == 1
    assert offset_put[0][2]["value"] == 43


def test_poll_once_no_updates_is_a_noop():
    fake_store = _FakeStore()

    with patch("telegram.polling.get_store", return_value=fake_store), \
         patch("telegram.polling._get_updates", return_value=[]):
        polling.poll_once()

    assert fake_store.put_calls == []
