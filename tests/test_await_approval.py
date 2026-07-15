import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unittest.mock import patch, MagicMock

from sunday.nodes.await_approval import proposal_worker, thread_id_for


class _FakeStore:
    def __init__(self):
        self.puts: list[tuple] = []

    def put(self, namespace, key, value):
        self.puts.append((namespace, key, value))


def _proposal_state():
    return {
        "proposal_id": "https://example.com/some-proposal",
        "decision": None,
        "message_id": None,
        "run_id": "run-abc123",
        "url": "https://example.com/some-proposal",
        "text": "full item text",
        "title": "Some Proposal",
        "tags": ["agentic-engineering"],
        "reasoning": "worth a project",
        "classification_reasoning": "routed as project_proposal",
        "proposal_type": "new",
        "matched_card_id": None,
    }


def test_proposal_worker_writes_pending_resume_map_with_real_message_id():
    """Confirms proposal_worker's store.put() call happens with the correct
    namespace, key (message_id), and value (thread_id) after the child
    proposal graph pauses on interrupt() -- covers await-approval-message-id-
    capture's mocked-unit-test verification clause."""
    fake_store = _FakeStore()
    fake_child = MagicMock()
    fake_child.invoke.return_value = {"message_id": 999888777}

    state = _proposal_state()
    expected_thread_id = thread_id_for(state["proposal_id"])

    with patch("sunday.nodes.await_approval.get_proposal_graph", return_value=fake_child), \
         patch("sunday.nodes.await_approval.get_store", return_value=fake_store):
        result = proposal_worker(state)

    assert len(fake_store.puts) == 1
    namespace, key, value = fake_store.puts[0]
    assert namespace == ("weekly_intel", "pending_resume_map")
    assert key == "999888777"
    assert value == {
        "thread_id": expected_thread_id,
        "proposal_id": state["proposal_id"],
        "run_id": state["run_id"],
    }
    assert result["pending_resumes"] == [{
        "proposal_id": state["proposal_id"],
        "thread_id": expected_thread_id,
        "message_id": 999888777,
    }]


def test_proposal_worker_invokes_child_graph_on_dedicated_thread():
    fake_store = _FakeStore()
    fake_child = MagicMock()
    fake_child.invoke.return_value = {"message_id": 111}

    state = _proposal_state()
    expected_thread_id = thread_id_for(state["proposal_id"])

    with patch("sunday.nodes.await_approval.get_proposal_graph", return_value=fake_child), \
         patch("sunday.nodes.await_approval.get_store", return_value=fake_store):
        proposal_worker(state)

    fake_child.invoke.assert_called_once()
    call_args, call_kwargs = fake_child.invoke.call_args
    assert call_args[0] == state
    assert call_kwargs["config"]["configurable"]["thread_id"] == expected_thread_id


def test_thread_id_for_is_deterministic_and_url_derived():
    a = thread_id_for("https://example.com/a")
    b = thread_id_for("https://example.com/a")
    c = thread_id_for("https://example.com/b")
    assert a == b
    assert a != c
    assert a.startswith("proposal-")
