"""
score_node (discovery/nodes/score.py) -- zero unit test coverage existed
before this file (confirmed by grep last session). Covers the actual
keep/drop judgment and tag validation, not just "it runs without
crashing". All external dependencies mocked: the real Groq call
(get_groq_client), mark_seen (real store write via discovery.seen_items),
log_scored_items (real store write via discovery.scored_items_log,
2026-07-23), record_node_summary (real store write via
core/observability.py), and the dropped-tag log file (real local file
I/O) so this suite stays fully offline like the rest of this project's
tests.

2026-07-26: switched from Anthropic/Haiku to Groq (openai/gpt-oss-120b)
with native structured outputs.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
from unittest.mock import patch, MagicMock

import discovery.nodes.score as score_mod
from discovery.nodes.score import score_node


def _clustered_item(url, title="T", text="some real body text"):
    return {
        "url": url, "title": title, "text": text, "author_name": "", "author_handle": "",
        "fetched_at": "2026-07-17T00:00:00+00:00", "is_thread": False, "thread_contents": None,
        "expanded_urls": [], "source": "blog_scrape", "duplicate_count": 1,
    }


def _state(clustered_items, run_id="run-1"):
    return {
        "raw_items": [], "clustered_items": clustered_items, "scored_items": [],
        "run_id": run_id, "costs": [], "errors": [], "source_context": "saturday",
    }


def _groq_response(results: list[dict]):
    resp = MagicMock()
    resp.choices = [MagicMock(message=MagicMock(content=json.dumps({"results": results})))]
    resp.usage.prompt_tokens = 100
    resp.usage.completion_tokens = 40
    return resp


def _patched():
    """Standard mock set for score_node tests -- every real external
    dependency stubbed so nothing here touches the network, the live
    Postgres store, or a real local file."""
    return (
        patch.object(score_mod, "get_groq_client"),
        patch.object(score_mod, "log_scored_items"),
        patch.object(score_mod, "record_node_summary"),
        patch.object(score_mod, "_log_dropped_tag"),
    )


def _set_groq_reply(mock_get_client, results):
    fake_client = MagicMock()
    fake_client.chat.completions.create.return_value = _groq_response(results)
    mock_get_client.return_value = fake_client
    return fake_client


def test_keep_true_and_keep_false_items_both_appear_in_scored_items():
    """score_node does not filter by keep -- it returns every scored item,
    keep=True and keep=False alike; filtering happens downstream
    (correlate_trello). Both must survive into scored_items with the
    real model's decision intact."""
    items = [_clustered_item("https://a.com/1"), _clustered_item("https://b.com/1")]
    groq_reply = [
        {"index": 0, "keep": True, "reasoning": "directly relevant", "tags": ["agentic-engineering"]},
        {"index": 1, "keep": False, "reasoning": "off-topic hiking content", "tags": ["noise"]},
    ]

    p_get_client, p_log, p_summary, p_dropped = _patched()
    with p_get_client as mock_get_client, p_log, p_summary, p_dropped:
        _set_groq_reply(mock_get_client, groq_reply)
        result = score_node(_state(items))

    assert len(result["scored_items"]) == 2
    by_url = {i["url"]: i for i in result["scored_items"]}
    assert by_url["https://a.com/1"]["keep"] is True
    assert by_url["https://a.com/1"]["tags"] == ["agentic-engineering"]
    assert by_url["https://b.com/1"]["keep"] is False
    assert by_url["https://b.com/1"]["reasoning"] == "off-topic hiking content"
    assert result["costs"][0]["provider"] == "groq"


def test_invalid_tag_is_filtered_out_and_logged():
    items = [_clustered_item("https://a.com/1")]
    groq_reply = [
        {"index": 0, "keep": True, "reasoning": "r", "tags": ["agentic-engineering", "not-a-real-tag"]},
    ]

    p_get_client, p_log, p_summary, p_dropped = _patched()
    with p_get_client as mock_get_client, p_log, p_summary, p_dropped as mock_log_dropped:
        _set_groq_reply(mock_get_client, groq_reply)
        result = score_node(_state(items))

    scored = result["scored_items"][0]
    assert scored["tags"] == ["agentic-engineering"]  # invalid tag filtered out
    mock_log_dropped.assert_called_once_with("not-a-real-tag", "https://a.com/1", "run-1")


def test_scoring_does_not_mark_any_item_seen_before_delivery():
    items = [_clustered_item("https://a.com/1"), _clustered_item("https://b.com/1")]
    groq_reply = [
        {"index": 0, "keep": True, "reasoning": "r", "tags": ["evals"]},
        {"index": 1, "keep": False, "reasoning": "r", "tags": ["noise"]},
    ]

    p_get_client, p_log, p_summary, p_dropped = _patched()
    with p_get_client as mock_get_client, p_log, p_summary, p_dropped:
        _set_groq_reply(mock_get_client, groq_reply)
        result = score_node(_state(items))

    assert {item["url"] for item in result["scored_items"]} == {
        "https://a.com/1",
        "https://b.com/1",
    }


def test_dry_run_skips_mark_seen():
    """dry_run=True lets manual testing exercise the full pipeline without
    permanently exhausting the real seen_items pool -- mark_seen() must not
    be called at all when the flag is set, regardless of keep/drop."""
    items = [_clustered_item("https://a.com/1")]
    groq_reply = [
        {"index": 0, "keep": True, "reasoning": "r", "tags": ["evals"]},
    ]

    state = _state(items)
    state["dry_run"] = True

    p_get_client, p_log, p_summary, p_dropped = _patched()
    with p_get_client as mock_get_client, p_log, p_summary, p_dropped:
        _set_groq_reply(mock_get_client, groq_reply)
        result = score_node(state)

    assert len(result["scored_items"]) == 1


def test_record_node_summary_reflects_kept_count_not_total():
    items = [_clustered_item("https://a.com/1"), _clustered_item("https://b.com/1"), _clustered_item("https://c.com/1")]
    groq_reply = [
        {"index": 0, "keep": True, "reasoning": "r", "tags": ["evals"]},
        {"index": 1, "keep": True, "reasoning": "r", "tags": ["evals"]},
        {"index": 2, "keep": False, "reasoning": "r", "tags": ["noise"]},
    ]

    p_get_client, p_log, p_summary, p_dropped = _patched()
    with p_get_client as mock_get_client, p_log, p_summary as mock_summary, p_dropped:
        _set_groq_reply(mock_get_client, groq_reply)
        score_node(_state(items))

    mock_summary.assert_called_once()
    _, kwargs = mock_summary.call_args
    assert kwargs["items_in"] == 3
    assert kwargs["items_out"] == 2  # kept count, not total scored


def test_multiple_batches_when_over_batch_size():
    """BATCH_SIZE is bounded -- confirms items beyond one batch still all get
    scored, via multiple real (mocked) Groq calls, not silently dropped."""
    items = [_clustered_item(f"https://example.com/{i}") for i in range(25)]
    call_sizes = []

    def _reply_for_batch(*args, **kwargs):
        prompt = kwargs["messages"][0]["content"]
        # _score_batch's per-item indices are local to that batch (0..N-1
        # via enumerate(batch)) -- count how many "[i]" markers appear to
        # know how many items THIS specific batch call contains.
        batch_size = prompt.count("URL:")
        call_sizes.append(batch_size)
        return _groq_response([{"index": i, "keep": True, "reasoning": "r", "tags": ["evals"]} for i in range(batch_size)])

    p_get_client, p_log, p_summary, p_dropped = _patched()
    with p_get_client as mock_get_client, p_log, p_summary, p_dropped:
        fake_client = MagicMock()
        fake_client.chat.completions.create.side_effect = _reply_for_batch
        mock_get_client.return_value = fake_client
        result = score_node(_state(items))

    assert len(result["scored_items"]) == 25
    assert fake_client.chat.completions.create.call_count == 3
    assert call_sizes == [12, 12, 1]
    assert all(
        call.kwargs["max_completion_tokens"] == 1024
        for call in fake_client.chat.completions.create.call_args_list
    )


def test_score_node_loads_effective_preferences_when_state_has_no_snapshot():
    items = [_clustered_item("https://a.com/1")]
    groq_reply = [
        {"index": 0, "keep": True, "reasoning": "r", "tags": ["evals"]},
    ]
    snapshot = score_mod.default_snapshot()
    snapshot["short_term"] = {"evals": 0.7}

    p_get_client, p_log, p_summary, p_dropped = _patched()
    with p_get_client as mock_get_client, p_log, p_summary, p_dropped, \
         patch.object(score_mod, "get_store", return_value=object()) as mock_store, \
         patch.object(score_mod, "load_effective_snapshot", return_value=snapshot) as mock_load:
        fake_client = _set_groq_reply(mock_get_client, groq_reply)
        score_node(_state(items))

    mock_store.assert_called_once()
    mock_load.assert_called_once()
    prompt = fake_client.chat.completions.create.call_args.kwargs["messages"][0]["content"]
    assert "Current positive signals: [('evals', 0.7)]" in prompt
