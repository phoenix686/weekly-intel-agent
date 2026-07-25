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
  {run_id, node_name, items_in, items_out, dropped, cost_usd, duration_seconds,
  langsmith_url, error_summary}.
- ("weekly_intel","run_history") -- one entry per entrypoint invocation
  (daily/sunday/poll): {run_id, started_at, finished_at, path, status,
  total_cost_usd, items_in, items_out, error_summary, duration_seconds}.

Every write here is wrapped in try/except and logged, never raised -- a
failed observability write must never mask or block the real run/node
outcome it's describing, same pattern already established for
classification_log/approval_log.

CRASH DURABILITY (2026-07-17, real 45-minute Sunday timeout): a
try/finally in the entrypoint script is NOT enough on its own. GitHub
Actions cancels a timed-out job via an external termination, not a
Python exception -- confirmed the run_history/node_summary namespaces
were both empty (bar one node_summary entry from the one node that
finished before the kill) after a real timeout, and confirmed via
GitHub's own community discussions that post-job steps (the same
category of "only runs if the job completes" behavior) are skipped
under a timeout cancellation for the identical reason. A finally block
deep inside a killed process may simply never get to run either.
record_run_started() exists for exactly this: called at the very start
of each entrypoint, before any risky work, so a real "in_progress"
record survives even a hard kill -- something is always better than
nothing. record_run_history() (called from the entrypoint's finally
block, as before) overwrites the same run_id key with the real final
status if the process survives long enough to get there. The absence of
that overwrite -- a run_history entry stuck at status="in_progress" --
is itself now a legible signal of "this run never finished," rather
than no record at all.

No langgraph imports.
"""

from __future__ import annotations

import logging
import time

from sunday.memory_store_config import get_store
from core.state import NodeCost

logger = logging.getLogger(__name__)

_NODE_SUMMARY_NAMESPACE = ("weekly_intel", "node_summary")
_RUN_HISTORY_NAMESPACE = ("weekly_intel", "run_history")


def cost_breakdown_by_provider(costs: list[NodeCost]) -> dict[str, float]:
    """Real per-run $ cost, broken out by which paid API incurred it --
    2026-07-26, added so a digest/plan footer (and a future model-provider
    swap's cost impact) shows attributable numbers, not one lump sum.
    Every real-cost NodeCost site sets provider ("anthropic" for every
    Claude call, "nvidia" for every embedding call); the many zero-cost
    sites (Telegram sends, Trello reads, formatting nodes) don't set it
    and fall into "other" here, which is always $0.00 in practice since
    none of those make a paid call. Always includes "total" as the sum
    of every record regardless of provider, so callers never need to
    re-derive it separately."""
    breakdown: dict[str, float] = {}
    for c in costs:
        provider = c.get("provider") or "other"
        breakdown[provider] = breakdown.get(provider, 0.0) + c["cost_usd"]
    breakdown["total"] = sum(c["cost_usd"] for c in costs)
    return breakdown


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
    duration_seconds: float = 0.0,
    error_summary: str | None = None,
) -> None:
    """One aggregate record per (run_id, node_name) -- items_in/items_out
    meaning is per-node (e.g. cluster_dedupe: raw_items in, clustered_items
    out; scrape_blogs: active source count in, raw_items fetched out).
    dropped is derived (items_in - items_out), not a second thing callers
    must compute themselves. duration_seconds defaults to 0.0 for
    callers that haven't been updated to pass their own measured
    latency -- real callers (cluster_dedupe, scrape_blogs, score_node,
    correlate_trello, classify_item) all pass their real elapsed time,
    computed the same way their own NodeCost.latency_ms already is."""
    try:
        store = get_store()
        logger.debug(f"observability: BEFORE store.put() (node_summary, {node_name}, run={run_id})")
        t0 = time.perf_counter()
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
                "duration_seconds": duration_seconds,
                "langsmith_url": get_current_trace_url(),
                "error_summary": error_summary,
            },
        )
        logger.debug(f"observability: AFTER store.put() (node_summary, {node_name}, run={run_id}) ({time.perf_counter() - t0:.3f}s)")
    except Exception as e:
        logger.warning(f"observability: node_summary write failed for {node_name} (run={run_id}): {e}")


def record_run_started(path: str, run_id: str, started_at: str) -> None:
    """Written at the very start of an entrypoint script, before any real
    work begins -- a real 'in_progress' record that survives even a hard
    external kill (a GitHub Actions timeout cancellation), which a
    finally block deep inside the same process might never get to run
    for. record_run_history() (called later, from the entrypoint's own
    finally block) overwrites this same run_id key with the real final
    outcome if the process survives that long. A run stuck at
    status='in_progress' with no later overwrite IS the signal that it
    never finished -- legible, not silence."""
    try:
        store = get_store()
        logger.debug(f"observability: BEFORE store.put() (run_history start-marker, {path}, run={run_id})")
        t0 = time.perf_counter()
        store.put(
            _RUN_HISTORY_NAMESPACE,
            run_id,
            {
                "run_id": run_id,
                "path": path,
                "started_at": started_at,
                "finished_at": None,
                "status": "in_progress",
                "total_cost_usd": 0.0,
                "items_in": 0,
                "items_out": 0,
                "duration_seconds": 0.0,
                "error_summary": None,
            },
        )
        logger.debug(f"observability: AFTER store.put() (run_history start-marker, {path}, run={run_id}) ({time.perf_counter() - t0:.3f}s)")
    except Exception as e:
        logger.warning(f"observability: run_history start-marker write failed for {path} run {run_id}: {e}")


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
        logger.debug(f"observability: BEFORE store.put() (run_history final, {path}, run={run_id})")
        t0 = time.perf_counter()
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
        logger.debug(f"observability: AFTER store.put() (run_history final, {path}, run={run_id}) ({time.perf_counter() - t0:.3f}s)")
    except Exception as e:
        logger.warning(f"observability: run_history write failed for {path} run {run_id}: {e}")
