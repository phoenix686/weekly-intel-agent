"""
Confirms topic-vector recompute is wired into the REAL place
taste_profile.yaml gets regenerated (sunday/approval_actions.py's
_update_yaml_for_feedback), not sunday/nodes/update_profile.py as
batch2-dedup-taste-spec.md's text literally says -- that file no longer
touches the YAML at all (moved here in an earlier Part 7 refactor).
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unittest.mock import patch, MagicMock

import sunday.approval_actions as approval_actions


def _fake_haiku_response(yaml_text: str):
    resp = MagicMock()
    resp.content = [MagicMock(text=yaml_text)]
    resp.usage.input_tokens = 100
    resp.usage.output_tokens = 50
    return resp


def test_update_yaml_for_feedback_recomputes_topic_vectors_after_write(tmp_path):
    fake_yaml_path = tmp_path / "taste_profile.yaml"
    new_yaml_text = "version: 1\nproposal_filters: []\nnotes: 'updated'"

    with patch.object(approval_actions, "TASTE_PROFILE_PATH", fake_yaml_path), \
         patch.object(approval_actions._client.messages, "create", return_value=_fake_haiku_response(new_yaml_text)), \
         patch.object(approval_actions, "recompute_topic_vectors", return_value=[]) as mock_recompute:
        approval_actions._update_yaml_for_feedback(
            {"url": "https://example.com/x", "text": "some content"},
            feedback_text="loved it",
            sentiment="positive",
        )

    # YAML was actually written first
    assert fake_yaml_path.read_text(encoding="utf-8") == new_yaml_text
    # recompute was called with the freshly-written text, exactly once, same call
    mock_recompute.assert_called_once_with(new_yaml_text)


def test_update_yaml_for_feedback_survives_topic_vector_recompute_failure(tmp_path):
    """Topic-vector recompute failing must not prevent the YAML write from
    having already succeeded (it happens first) -- but a raised exception
    here would still propagate up, since recompute_topic_vectors itself
    is the one responsible for per-tag graceful degradation, not this
    caller swallowing a total failure silently."""
    fake_yaml_path = tmp_path / "taste_profile.yaml"
    new_yaml_text = "version: 1\nproposal_filters: []\nnotes: 'updated'"

    with patch.object(approval_actions, "TASTE_PROFILE_PATH", fake_yaml_path), \
         patch.object(approval_actions._client.messages, "create", return_value=_fake_haiku_response(new_yaml_text)), \
         patch.object(approval_actions, "recompute_topic_vectors", return_value=[
             {"node_name": "recompute_topic_vectors", "input_tokens": 0, "output_tokens": 0,
              "cost_usd": 0.0, "latency_ms": 0.0, "error": "recompute failed for tag 'evals': API down"},
         ]):
        approval_actions._update_yaml_for_feedback(
            {"url": "https://example.com/x", "text": "some content"},
            feedback_text="loved it",
            sentiment="positive",
        )

    # YAML write already happened and persists regardless of recompute outcome
    assert fake_yaml_path.read_text(encoding="utf-8") == new_yaml_text
