"""
observability.py (2026-07-17): durable, queryable run/node summaries --
the thin-log design confirmed against a real 108-item LangSmith trace
(no truncation, full per-item detail survives intact), so this
deliberately records aggregate counts + a trace pointer, not per-item
detail (that stays in LangSmith + prefilter_drops).
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unittest.mock import patch, MagicMock

import observability


class _FakeStore:
    def __init__(self, raise_on_put=False):
        self.puts: list[tuple] = []
        self._raise_on_put = raise_on_put

    def put(self, namespace, key, value):
        if self._raise_on_put:
            raise RuntimeError("simulated store failure")
        self.puts.append((namespace, key, value))


def test_record_node_summary_writes_correct_shape_with_derived_dropped_count():
    fake_store = _FakeStore()
    with patch.object(observability, "get_store", return_value=fake_store), \
         patch.object(observability, "get_current_trace_url", return_value="https://smith.langchain.com/fake-trace"):
        observability.record_node_summary(
            run_id="run-1", node_name="cluster_dedupe", items_in=108, items_out=16, cost_usd=0.0,
            duration_seconds=12.345,
        )

    puts = [p for p in fake_store.puts if p[0] == observability._NODE_SUMMARY_NAMESPACE]
    assert len(puts) == 1
    _, key, value = puts[0]
    assert key == "run-1:cluster_dedupe"
    assert value["run_id"] == "run-1"
    assert value["node_name"] == "cluster_dedupe"
    assert value["items_in"] == 108
    assert value["items_out"] == 16
    assert value["dropped"] == 92  # derived, not passed in by the caller
    assert value["duration_seconds"] == 12.345
    assert value["langsmith_url"] == "https://smith.langchain.com/fake-trace"
    assert value["error_summary"] is None


def test_record_node_summary_duration_seconds_defaults_to_zero():
    """Callers that haven't been updated to pass their own measured
    latency still produce a valid record, just with duration_seconds=0.0
    rather than a missing field."""
    fake_store = _FakeStore()
    with patch.object(observability, "get_store", return_value=fake_store), \
         patch.object(observability, "get_current_trace_url", return_value=None):
        observability.record_node_summary(run_id="run-1", node_name="some_node", items_in=5, items_out=5)

    _, _, value = fake_store.puts[0]
    assert value["duration_seconds"] == 0.0


def test_record_node_summary_failed_write_does_not_raise():
    fake_store = _FakeStore(raise_on_put=True)
    with patch.object(observability, "get_store", return_value=fake_store), \
         patch.object(observability, "get_current_trace_url", return_value=None):
        observability.record_node_summary(run_id="run-1", node_name="scrape_blogs", items_in=12, items_out=58)  # must not raise


def test_record_run_history_writes_correct_shape():
    fake_store = _FakeStore()
    with patch.object(observability, "get_store", return_value=fake_store):
        observability.record_run_history(
            path="sunday", run_id="run-2", started_at="2026-07-17T00:00:00+00:00",
            finished_at="2026-07-17T00:05:00+00:00", status="success",
            total_cost_usd=0.0123, items_in=40, items_out=9,
            duration_seconds=300.0, error_summary=None,
        )

    puts = [p for p in fake_store.puts if p[0] == observability._RUN_HISTORY_NAMESPACE]
    assert len(puts) == 1
    _, key, value = puts[0]
    assert key == "run-2"
    assert value["path"] == "sunday"
    assert value["status"] == "success"
    assert value["items_in"] == 40
    assert value["items_out"] == 9
    assert value["duration_seconds"] == 300.0
    assert value["error_summary"] is None


def test_record_run_history_failed_write_does_not_raise():
    fake_store = _FakeStore(raise_on_put=True)
    with patch.object(observability, "get_store", return_value=fake_store):
        observability.record_run_history(
            path="daily", run_id="run-3", started_at="x", finished_at="y", status="failed",
            total_cost_usd=0.0, items_in=0, items_out=0, duration_seconds=1.0,
            error_summary="FileNotFoundError: data/tweets.json",
        )  # must not raise


def test_record_run_started_writes_in_progress_marker():
    """The crash-durability write -- called before any real work starts,
    so a real record survives even a hard external kill that a finally
    block deep in the same process might never get to run for."""
    fake_store = _FakeStore()
    with patch.object(observability, "get_store", return_value=fake_store):
        observability.record_run_started(path="sunday", run_id="run-4", started_at="2026-07-17T00:00:00+00:00")

    puts = [p for p in fake_store.puts if p[0] == observability._RUN_HISTORY_NAMESPACE]
    assert len(puts) == 1
    _, key, value = puts[0]
    assert key == "run-4"  # same key record_run_history uses -- a later real call overwrites this
    assert value["status"] == "in_progress"
    assert value["finished_at"] is None
    assert value["started_at"] == "2026-07-17T00:00:00+00:00"


def test_record_run_started_is_overwritten_by_record_run_history_same_key():
    """Confirms the real overwrite semantics: record_run_started's
    in_progress marker and record_run_history's final write target the
    exact same store key (run_id), so a completed run leaves ONE final
    record, not two -- and a run that never finishes leaves the
    in_progress marker as the last word."""
    fake_store = _FakeStore()
    with patch.object(observability, "get_store", return_value=fake_store):
        observability.record_run_started(path="sunday", run_id="run-5", started_at="t0")
        observability.record_run_history(
            path="sunday", run_id="run-5", started_at="t0", finished_at="t1", status="success",
            total_cost_usd=0.01, items_in=10, items_out=3, duration_seconds=5.0,
        )

    puts = [p for p in fake_store.puts if p[0] == observability._RUN_HISTORY_NAMESPACE]
    assert len(puts) == 2  # two real store.put() calls...
    assert puts[0][1] == puts[1][1] == "run-5"  # ...to the exact same key (a real store overwrites, doesn't duplicate)
    assert puts[1][2]["status"] == "success"  # the final call's value is what a real store would end up holding


def test_record_run_started_failed_write_does_not_raise():
    fake_store = _FakeStore(raise_on_put=True)
    with patch.object(observability, "get_store", return_value=fake_store):
        observability.record_run_started(path="poll", run_id="run-6", started_at="t0")  # must not raise


def test_get_current_trace_url_returns_none_when_no_run_context():
    with patch("langsmith.run_helpers.get_current_run_tree", return_value=None):
        assert observability.get_current_trace_url() is None


def test_get_current_trace_url_returns_real_url_when_tracing_active():
    fake_run_tree = MagicMock()
    fake_run_tree.get_url.return_value = "https://smith.langchain.com/real-trace-url"
    with patch("langsmith.run_helpers.get_current_run_tree", return_value=fake_run_tree):
        assert observability.get_current_trace_url() == "https://smith.langchain.com/real-trace-url"


def test_get_current_trace_url_swallows_lookup_failure():
    with patch("langsmith.run_helpers.get_current_run_tree", side_effect=RuntimeError("no context")):
        assert observability.get_current_trace_url() is None
