"""
Phase 0 smoke test.

Run this to prove, end to end:
  1. core/state.py's schema is real and instantiable (not just aspirational)
  2. the discovery subgraph skeleton compiles
  3. a trivial run executes node-by-node in the right order
  4. the compiled graph can emit its own Mermaid source (for rendering)

Usage:
    python scripts/smoke_test_phase0.py
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")  # Windows console defaults to cp1252,
    # which can't print emoji that show up in real digest/plan text -- unrelated to
    # graph logic, just an output-encoding fix so this script can run to completion.

from discovery.graph import build_discovery_subgraph, make_initial_state


def main():
    print("=== Phase 0 smoke test ===\n")

    # 1. Prove state schema is instantiable
    initial_state = make_initial_state()
    print("Initial state:")
    print(initial_state)
    print()

    # 2. Compile the graph
    graph = build_discovery_subgraph()
    print("Graph compiled OK.\n")

    # 3. Run it once
    print("Running graph...")
    final_state = graph.invoke(initial_state)
    print("\nFinal state:")
    print(final_state)
    print()

    # NOTE (smoke-test-node-count-fix, Checkpoint 5): the original hardcoded
    # 'assert len(final_state["costs"]) == 3' assumed one NodeCost per node.
    # That's no longer true -- semantic_dedup and taste_prefilter (Checkpoint
    # 5) each append one NodeCost PER ITEM they process, so total cost-record
    # count now scales with how much survives the dedup/seen_items filters
    # ahead of them, not a fixed per-node constant. The real, stable
    # invariant is: the three always-present, one-record-per-invocation
    # nodes (scrape_blogs/process_adhoc_input's source node, cluster_dedupe's
    # own base record, score_node) each appear at least once.
    cost_node_names = {c["node_name"] for c in final_state["costs"]}
    assert "cluster_dedupe" in cost_node_names, f"Expected cluster_dedupe in costs, got: {cost_node_names}"
    assert "score_node" in cost_node_names, f"Expected score_node in costs, got: {cost_node_names}"
    print(f"Assertions passed: {len(final_state['costs'])} cost record(s) total, node names present: {sorted(cost_node_names)}\n")

    # final_state["stage"] now really advances: scrape_blogs/process_adhoc_input
    # write "sourced", cluster_dedupe_node writes "clustered", score_node
    # writes "scored" -- score_node's own "scored" is the natural terminal
    # marker (no separate "done" value). "stage" uses a last-write-wins
    # reducer (core/state.py's _last_write_wins) because on Sunday runs
    # scrape_blogs and process_adhoc_input both write "sourced" in the same
    # superstep -- a plain key would raise InvalidUpdateError on that
    # concurrent write, the same class of bug operator.add fixed for 'errors'.
    assert final_state["stage"] == "scored", f"Expected final stage 'scored', got: {final_state['stage']!r}"
    print(f"Assertion passed: final_state['stage'] == {final_state['stage']!r}\n")

    # 4. Emit Mermaid source for rendering
    mermaid_src = graph.get_graph().draw_mermaid()
    print("=== Mermaid source (paste into renderer) ===")
    print(mermaid_src)

    # Save it too, so it's a repo artifact, not just stdout
    out_path = os.path.join(os.path.dirname(__file__), "..", "discovery_graph.mmd")
    with open(out_path, "w") as f:
        f.write(mermaid_src)
    print(f"\nSaved to {os.path.abspath(out_path)}")


if __name__ == "__main__":
    main()
