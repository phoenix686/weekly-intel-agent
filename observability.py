"""
Durable, queryable per-run/per-node summaries -- the thin-log design
confirmed 2026-07-17: LangSmith already holds the full per-item detail at
real scale (verified against a real 108-item trace, no truncation, full
similarity-score/drop-reason strings intact), so this module deliberately
does NOT duplicate per-item detail. It records aggregate counts plus a
pointer to the real LangSmith trace for that node, and the granular WHY
stays queryable directly from ("weekly_intel","prefilter_drops") for the
rare cases someone needs to dig that deep.

Two namespaces:
- ("weekly_intel","node_summary") -- one entry per (run_id, node_name):
  {run_id, node_name, items_in, items_out, dropped, cost_usd, langsmith_url,
  error_summary}.
- ("weekly_intel","run_history") -- one entry per entrypoint invocation
  (daily/sunday/poll): {run_id, started_at, finished_at, path, status,
  total_cost_usd, items_in, items_out, error_summary, duration_seconds}.

Every write here is wrapped in try/except and logged, never raised -- a
failed observability write must never mask or block the real run/node
outcome it's describing, same pattern already established for
classification_log/approval_log.

No langgraph imports.
"""

from __future__ import annotations

import logging

from sunday.memory_store_config import get_store

logger = logging.getLogger(__name__)

_NODE_SUMMARY_NAMESPACE = ("weekly_intel", "node_summary")
_RUN_HISTORY_NAMESPACE = ("weekly_intel", "run_history")


def get_current_trace_url() -> str | None:
    """Real LangSmith trace URL for whatever run is currently executing,
    if tracing is active -- None if tracing is off or no run context
    exists (e.g. a bare unit test calling a node function directly,
    outside any graph.invoke()). Never raises -- any lookup failure just
    means no pointer is available, not a broken node."""
    try:
        from langsmith.run_helpers import get_current_run_tree
        run_tree = get_current_run_tree()
        if run_tree is None:
            return None
        return run_tree.get_url()
    except Exception as e:
        logger.debug(f"observability: could not resolve current LangSmith trace URL: {e}")
        return None


def record_node_summary(
    run_id: str,
    node_name: str,
    items_in: int,
    items_out: int,
    cost_usd: float = 0.0,
    error_summary: str | None = None,
) -> None:
    """One aggregate record per (run_id, node_name) -- items_in/items_out
    meaning is per-node (e.g. cluster_dedupe: raw_items in, clustered_items
    out; scrape_blogs: active source count in, raw_items fetched out).
    dropped is derived (items_in - items_out), not a second thing callers
    must compute themselves."""
    try:
        store = get_store()
        store.put(
            _NODE_SUMMARY_NAMESPACE,
            f"{run_id}:{node_name}",
            {
                "run_id": run_id,
                "node_name": node_name,
                "items_in": items_in,
                "items_out": items_out,
                "dropped": items_in - items_out,
                "cost_usd": cost_usd,
                "langsmith_url": get_current_trace_url(),
                "error_summary": error_summary,
            },
        )
    except Exception as e:
        logger.warning(f"observability: node_summary write failed for {node_name} (run={run_id}): {e}")


def record_run_history(
    path: str,
    run_id: str,
    started_at: str,
    finished_at: str,
    status: str,
    total_cost_usd: float,
    items_in: int,
    items_out: int,
    duration_seconds: float,
    error_summary: str | None = None,
) -> None:
    """One record per entrypoint invocation (path: 'daily'|'sunday'|'poll').
    Written once, at the very end of the entrypoint script -- callers are
    responsible for wrapping their real work in try/except/finally so this
    still gets called (with status='failed' and a real error_summary) even
    when the run crashes, not just on a clean finish (cost_log.csv's exact
    blind spot, which this replaces for the same use case)."""
    try:
        store = get_store()
        store.put(
            _RUN_HISTORY_NAMESPACE,
            run_id,
            {
                "run_id": run_id,
                "path": path,
                "started_at": started_at,
                "finished_at": finished_at,
                "status": status,
                "total_cost_usd": total_cost_usd,
                "items_in": items_in,
                "items_out": items_out,
                "duration_seconds": duration_seconds,
                "error_summary": error_summary,
            },
        )
    except Exception as e:
        logger.warning(f"observability: run_history write failed for {path} run {run_id}: {e}")
