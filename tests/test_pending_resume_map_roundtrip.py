"""
Confirms proposal_worker's pending_resume_map write round-trips through the
real PostgresStore: namespace=("weekly_intel", "pending_resume_map"), keyed
by the (mocked) Telegram message_id, value={thread_id, proposal_id, run_id}.

Mocks only the Telegram send + child graph invoke (so no real Telegram
message is sent and no interrupt() actually pauses) -- the store write/read
is real. Cleans up the test key after asserting.

Requires DB_URI (Postgres/Supabase) to be reachable -- this sandbox has no
.env, so this script is written for Pooja to run locally:
  uv run --env-file .env python scripts/test_pending_resume_map_roundtrip.py
"""

import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

from sunday.nodes.await_approval import proposal_worker, thread_id_for
from sunday.memory_store_config import get_store

TEST_MESSAGE_ID = 900000001
TEST_PROPOSAL_ID = "https://example.com/pending-resume-map-roundtrip-test"


def test_pending_resume_map_roundtrip() -> bool:
    state = {
        "proposal_id": TEST_PROPOSAL_ID,
        "decision": None,
        "message_id": None,
        "run_id": "pending-resume-map-roundtrip-test",
        "url": TEST_PROPOSAL_ID,
        "text": "synthetic item for pending_resume_map round-trip verification",
        "title": "Round-trip test proposal",
        "tags": ["test"],
        "reasoning": "test",
        "classification_reasoning": "test",
        "proposal_type": "new",
        "matched_card_id": None,
    }

    fake_child = MagicMock()
    fake_child.invoke.return_value = {"message_id": TEST_MESSAGE_ID}

    with patch("sunday.nodes.await_approval.get_proposal_graph", return_value=fake_child):
        proposal_worker(state)

    store = get_store()
    stored = store.get(("weekly_intel", "pending_resume_map"), str(TEST_MESSAGE_ID))

    expected_thread_id = thread_id_for(TEST_PROPOSAL_ID)
    ok = (
        stored is not None
        and stored.value.get("thread_id") == expected_thread_id
        and stored.value.get("proposal_id") == TEST_PROPOSAL_ID
        and stored.value.get("run_id") == "pending-resume-map-roundtrip-test"
    )
    print(f"pending_resume_map round-trip: {'PASS' if ok else 'FAIL'}")
    if stored:
        print(f"  stored value: {stored.value}")

    store.delete(("weekly_intel", "pending_resume_map"), str(TEST_MESSAGE_ID))
    return ok


if __name__ == "__main__":
    ok = test_pending_resume_map_roundtrip()
    print("\nVERDICT:", "PASS" if ok else "FAIL")
