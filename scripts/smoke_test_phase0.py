"""
Phase 0 smoke test.

Run this to prove, end to end:
  1. state.py's schema is real and instantiable (not just aspirational)
  2. the discovery subgraph skeleton compiles
  3. a trivial run executes node-by-node in the right order
  4. the compiled graph can emit its own Mermaid source (for rendering)

Usage:
    python scripts/smoke_test_phase0.py
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

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

    assert final_state["stage"] == "scored", "Graph did not reach final stage!"
    assert len(final_state["costs"]) == 3, "Expected 3 cost records (one per node)!"
    print("Assertions passed: graph ran all 3 nodes, reached final stage.\n")

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
