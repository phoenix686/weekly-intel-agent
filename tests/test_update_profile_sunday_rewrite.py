"""
sunday-consolidated-taste-rewrite: sunday/nodes/update_profile.py's
restored real rewrite logic, per batch2-dedup-taste-spec.md Section 7,
item 2 (confirmed file-layout: this file, not approval_actions.py).

SCOPE: this test file owns cadence/call-site correctness (one Haiku call
regardless of record count, runs once per invocation, clears
same_day_adjustments, excludes out-of-window records). The per-tag
embedding/mapping mechanics of recompute_topic_vectors itself are
tested in tests/test_taste_vectors.py, not duplicated here.

2026-07-26: taste_profile persistence moved to Postgres (discovery/
taste_profile_store.py) -- these tests now patch get_taste_profile/
put_taste_profile directly instead of TASTE_PROFILE_PATH/a tmp_path
file. Every test here also patches COST_LOG_PATH to a tmp_path file --
previously unpatched (it was a hardcoded local Path() inside the
function, not a module attribute), so every local pytest run of this
file appended real rows to the real data/cost_log.csv.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timedelta, timezone
from unittest.mock import patch, MagicMock

import sunday.nodes.update_profile as update_profile_mod
from sunday.nodes.update_profile import update_profile


class _Item:
    def __init__(self, key, value):
        self.key = key
        self.value = value


class _FakeStore:
    def __init__(self, feedback_seed: dict | None = None, same_day_seed: dict | None = None):
        self._feedback = dict(feedback_seed or {})
        self._same_day = dict(same_day_seed or {})
        self.deleted: list[tuple] = []

    def search(self, namespace, limit=500):
        if namespace == update_profile_mod._FEEDBACK_NAMESPACE:
            return [_Item(k, v) for k, v in self._feedback.items()][:limit]
        if namespace == update_profile_mod._SAME_DAY_NAMESPACE:
            return [_Item(k, v) for k, v in self._same_day.items()][:limit]
        return []

    def delete(self, namespace, key):
        self.deleted.append((namespace, key))
        if namespace == update_profile_mod._SAME_DAY_NAMESPACE:
            self._same_day.pop(key, None)


def _feedback_record(item_id, feedback_text, replied_at, tags=None):
    return {
        "item_id": item_id, "feedback_text": feedback_text, "replied_at": replied_at,
        "run_id": "some-run", "tags": tags or [], "title": "T", "content_summary": "summary",
        "sentiment": "positive",
    }


def _haiku_response(yaml_text: str):
    resp = MagicMock()
    resp.content = [MagicMock(text=yaml_text)]
    resp.usage.input_tokens = 200
    resp.usage.output_tokens = 80
    return resp


def _state(run_id="sunday-run-1"):
    return {
        "run_id": run_id, "scored_items": [], "trello_cards": [], "correlated_items": [],
        "classified_items": [], "plan_text": "", "plan_item_map": {}, "pending_approvals": [],
        "pending_resumes": [], "costs": [], "errors": [], "source_context": "sunday",
    }


def test_multiple_records_produce_exactly_one_haiku_call(tmp_path):
    now = datetime.now(timezone.utc)
    fake_store = _FakeStore(feedback_seed={
        "a": _feedback_record("https://a.com", "loved it", now.isoformat(), ["agentic-engineering"]),
        "b": _feedback_record("https://b.com", "meh", now.isoformat(), ["evals"]),
        "c": _feedback_record("https://c.com", "great", now.isoformat(), ["memory-systems"]),
    })
    new_yaml = "version: 1\nproposal_filters: []\nnotes: 'weekly update'"

    with patch.object(update_profile_mod, "COST_LOG_PATH", tmp_path / "cost_log.csv"), \
         patch.object(update_profile_mod, "get_store", return_value=fake_store), \
         patch.object(update_profile_mod, "get_taste_profile", return_value=None), \
         patch.object(update_profile_mod, "put_taste_profile") as mock_put, \
         patch.object(update_profile_mod._client.messages, "create", return_value=_haiku_response(new_yaml)) as mock_create, \
         patch.object(update_profile_mod, "recompute_topic_vectors", return_value=[]) as mock_recompute:
        result = update_profile(_state())

    assert mock_create.call_count == 1  # ONE consolidated call, not one per record
    mock_put.assert_called_once_with(new_yaml)
    mock_recompute.assert_called_once_with(new_yaml)
    # prompt actually contains all three records, proving it's consolidated not just first-one-wins
    prompt_text = mock_create.call_args.kwargs["messages"][0]["content"]
    assert "a.com" in prompt_text and "b.com" in prompt_text and "c.com" in prompt_text


def test_current_profile_read_from_postgres_not_placeholder_when_one_exists(tmp_path):
    """Real gap this fix closes: the prior profile must come from Postgres
    (get_taste_profile), not always fall back to the placeholder -- a
    fresh runner with no local file must still see the real current
    profile if Postgres has one."""
    now = datetime.now(timezone.utc)
    fake_store = _FakeStore(feedback_seed={
        "a": _feedback_record("https://a.com", "loved it", now.isoformat()),
    })
    existing_profile = "version: 3\nproposal_filters: [{tag: evals, weight: 0.8}]\nnotes: 'real prior profile'"

    with patch.object(update_profile_mod, "COST_LOG_PATH", tmp_path / "cost_log.csv"), \
         patch.object(update_profile_mod, "get_store", return_value=fake_store), \
         patch.object(update_profile_mod, "get_taste_profile", return_value=existing_profile), \
         patch.object(update_profile_mod, "put_taste_profile"), \
         patch.object(update_profile_mod._client.messages, "create", return_value=_haiku_response("version: 4")) as mock_create, \
         patch.object(update_profile_mod, "recompute_topic_vectors", return_value=[]):
        update_profile(_state())

    prompt_text = mock_create.call_args.kwargs["messages"][0]["content"]
    assert "real prior profile" in prompt_text


def test_same_day_adjustments_cleared_after_rewrite(tmp_path):
    now = datetime.now(timezone.utc)
    fake_store = _FakeStore(
        feedback_seed={"a": _feedback_record("https://a.com", "x", now.isoformat())},
        same_day_seed={"2026-W29:evals": {"tag": "evals", "cumulative_adjustment": 0.15, "item_ids_contributing": ["x"], "week_of": "2026-W29"}},
    )

    with patch.object(update_profile_mod, "COST_LOG_PATH", tmp_path / "cost_log.csv"), \
         patch.object(update_profile_mod, "get_store", return_value=fake_store), \
         patch.object(update_profile_mod, "get_taste_profile", return_value=None), \
         patch.object(update_profile_mod, "put_taste_profile"), \
         patch.object(update_profile_mod._client.messages, "create", return_value=_haiku_response("version: 1")), \
         patch.object(update_profile_mod, "recompute_topic_vectors", return_value=[]):
        update_profile(_state())

    assert fake_store._same_day == {}
    assert (update_profile_mod._SAME_DAY_NAMESPACE, "2026-W29:evals") in fake_store.deleted


def test_record_from_prior_week_excluded_from_prompt(tmp_path):
    now = datetime.now(timezone.utc)
    stale = (now - timedelta(days=10)).isoformat()
    fake_store = _FakeStore(feedback_seed={
        "old": _feedback_record("https://old.com", "old feedback", stale),
        "new": _feedback_record("https://new.com", "new feedback", now.isoformat()),
    })

    with patch.object(update_profile_mod, "COST_LOG_PATH", tmp_path / "cost_log.csv"), \
         patch.object(update_profile_mod, "get_store", return_value=fake_store), \
         patch.object(update_profile_mod, "get_taste_profile", return_value=None), \
         patch.object(update_profile_mod, "put_taste_profile"), \
         patch.object(update_profile_mod._client.messages, "create", return_value=_haiku_response("version: 1")) as mock_create, \
         patch.object(update_profile_mod, "recompute_topic_vectors", return_value=[]):
        update_profile(_state())

    prompt_text = mock_create.call_args.kwargs["messages"][0]["content"]
    assert "new.com" in prompt_text
    assert "old.com" not in prompt_text


def test_no_feedback_since_last_sunday_skips_rewrite_entirely(tmp_path):
    fake_store = _FakeStore(feedback_seed={})

    with patch.object(update_profile_mod, "COST_LOG_PATH", tmp_path / "cost_log.csv"), \
         patch.object(update_profile_mod, "get_store", return_value=fake_store), \
         patch.object(update_profile_mod, "get_taste_profile") as mock_get, \
         patch.object(update_profile_mod, "put_taste_profile") as mock_put, \
         patch.object(update_profile_mod._client.messages, "create") as mock_create, \
         patch.object(update_profile_mod, "recompute_topic_vectors") as mock_recompute:
        update_profile(_state())

    mock_create.assert_not_called()
    mock_recompute.assert_not_called()
    mock_get.assert_not_called()  # zero-cost skip -- never even reads the current profile
    mock_put.assert_not_called()


def test_cost_log_write_uses_patched_path_not_the_real_local_file(tmp_path):
    """Direct regression test for the pollution bug: COST_LOG_PATH must
    actually be a patchable module attribute the function reads, not a
    hardcoded Path() literal inside the function body."""
    fake_store = _FakeStore(feedback_seed={})
    fake_cost_log = tmp_path / "cost_log.csv"

    with patch.object(update_profile_mod, "COST_LOG_PATH", fake_cost_log), \
         patch.object(update_profile_mod, "get_store", return_value=fake_store):
        update_profile(_state(run_id="test-run-xyz"))

    assert fake_cost_log.exists()
    content = fake_cost_log.read_text(encoding="utf-8")
    assert "test-run-xyz" in content
