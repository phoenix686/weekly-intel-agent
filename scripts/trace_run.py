"""
Given a run_id, queries every relevant weekly_intel namespace
(run_history, node_summary, classification_log, approval_log,
prefilter_drops) and prints one unified, readable report of what
happened at each stage, in order, with real numbers -- the actual fix
for "I have to manually check multiple Postgres tables to debug a run."

Usage: uv run --env-file .env python scripts/trace_run.py <run_id>
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

from saturday.memory_store_config import get_store

# Real node execution order for a Saturday run (saturday/graph.py):
# discovery_subgraph (scrape_blogs -> cluster_dedupe -> score_node) ->
# read_trello -> correlate_trello -> classify_item -> assemble_plan.
_NODE_ORDER = [
    "scrape_blogs", "cluster_dedupe", "score_node",
    "correlate_trello", "classify_item",
]


def _fmt_cost(v: float) -> str:
    return f"${v:.6f}" if v else "$0.00"


def trace_run(run_id: str) -> None:
    store = get_store()

    print(f"{'=' * 70}")
    print(f"RUN TRACE: {run_id}")
    print(f"{'=' * 70}")

    # --- run_history: the top-level entrypoint record ---
    run_entry = store.get(("weekly_intel", "run_history"), run_id)
    print("\n--- run_history ---")
    if run_entry is None:
        print("  NO run_history entry found for this run_id.")
    else:
        v = run_entry.value
        print(f"  path: {v.get('path')}")
        print(f"  status: {v.get('status')}")
        print(f"  started_at:  {v.get('started_at')}")
        print(f"  finished_at: {v.get('finished_at')}")
        print(f"  duration_seconds: {v.get('duration_seconds')}")
        print(f"  total_cost_usd: {_fmt_cost(v.get('total_cost_usd', 0.0))}")
        print(f"  items_in/items_out: {v.get('items_in')}/{v.get('items_out')}")
        if v.get("error_summary"):
            print(f"  error_summary: {v['error_summary']}")

    # --- node_summary: one entry per (run_id, node_name), in real graph order ---
    print("\n--- node_summary (in real execution order) ---")
    all_node_entries = store.search(("weekly_intel", "node_summary"), limit=2000)
    by_node = {
        e.value["node_name"]: e.value
        for e in all_node_entries
        if e.value.get("run_id") == run_id
    }
    seen_nodes = set()
    for node_name in _NODE_ORDER:
        if node_name in by_node:
            v = by_node[node_name]
            seen_nodes.add(node_name)
            error = f"  [ERROR: {v['error_summary']}]" if v.get("error_summary") else ""
            print(
                f"  {node_name:16s} items_in={v['items_in']:<4} items_out={v['items_out']:<4} "
                f"dropped={v['dropped']:<4} cost={_fmt_cost(v['cost_usd']):<12} "
                f"duration={v.get('duration_seconds', 0.0):.2f}s{error}"
            )
    for node_name, v in by_node.items():
        if node_name not in seen_nodes:
            error = f"  [ERROR: {v['error_summary']}]" if v.get("error_summary") else ""
            print(f"  {node_name:16s} (not in expected order) items_in={v['items_in']} items_out={v['items_out']}{error}")
    if not by_node:
        print("  No node_summary entries found for this run_id.")

    # --- classification_log: per-item classify_item decisions ---
    print("\n--- classification_log ---")
    class_entries = [
        e.value for e in store.search(("weekly_intel", "classification_log"), limit=2000)
        if e.value.get("run_id") == run_id
    ]
    if class_entries:
        plan_items = [c for c in class_entries if c["decision"] == "plan_item"]
        proposals = [c for c in class_entries if c["decision"] == "project_proposal"]
        print(f"  {len(class_entries)} classified: {len(plan_items)} plan_item, {len(proposals)} project_proposal")
        for c in proposals:
            print(f"    proposal ({c.get('proposal_type')}): {c['item_id']}")
    else:
        print("  No classification_log entries found for this run_id.")

    # --- approval_log: real human approve/reject outcomes tied to this run ---
    print("\n--- approval_log ---")
    approval_entries = [
        e.value for e in store.search(("weekly_intel", "approval_log"), limit=2000)
        if e.value.get("run_id") == run_id
    ]
    if approval_entries:
        for a in approval_entries:
            print(f"  {a['outcome']}: {a['item_id']}")
    else:
        print("  No approval_log entries found for this run_id.")

    # --- prefilter_drops: per-item dedup/taste-filter drops ---
    print("\n--- prefilter_drops ---")
    drop_entries = [
        e.value for e in store.search(("weekly_intel", "prefilter_drops"), limit=2000)
        if e.value.get("run_id") == run_id
    ]
    if drop_entries:
        dedup_drops = [d for d in drop_entries if d["filter_type"] == "dedup"]
        taste_drops = [d for d in drop_entries if d["filter_type"] == "taste"]
        print(f"  {len(drop_entries)} dropped: {len(dedup_drops)} dedup, {len(taste_drops)} taste-prefilter")
        for d in drop_entries[:10]:
            target = d.get("compared_against_item_id") or d.get("compared_against_tag")
            print(f"    [{d['filter_type']}] {d['item_id']} (cosine={d['similarity_score']:.3f} vs {target})")
        if len(drop_entries) > 10:
            print(f"    ... and {len(drop_entries) - 10} more")
    else:
        print("  No prefilter_drops entries found for this run_id.")

    print(f"\n{'=' * 70}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: uv run --env-file .env python scripts/trace_run.py <run_id>")
        sys.exit(1)
    trace_run(sys.argv[1])
