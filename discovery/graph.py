"""
Discovery subgraph — Phase 1.

search_scrape_node (Phase 0 placeholder) is replaced by the real
ingest_bookmarks node. cluster_dedupe_node and score_node remain Phase 0
no-op placeholders until Phase 2.
"""

from __future__ import annotations

import time
import uuid

from langgraph.graph import StateGraph, START, END

from state import DiscoverySubgraphState, NodeCost
from discovery.nodes.ingest_bookmarks import ingest_bookmarks


def _make_cost_record(node_name: str, start_time: float) -> NodeCost:
    return NodeCost(
        node_name=node_name,
        input_tokens=0,
        output_tokens=0,
        latency_ms=round((time.perf_counter() - start_time) * 1000, 4),
    )


def cluster_dedupe_node(state: DiscoverySubgraphState) -> dict:
    """Placeholder for: dedupe near-duplicate RawItems -> ClusteredItem list."""
    t0 = time.perf_counter()
    print(f"[node] cluster_dedupe_node running (run_id={state['run_id']})")

    return {
        "clustered_items": [],
        "stage": "clustered",
        "costs": [_make_cost_record("cluster_dedupe_node", t0)],
    }


def score_node(state: DiscoverySubgraphState) -> dict:
    """Placeholder for: score ClusteredItems against taste profile -> ScoredItem list."""
    t0 = time.perf_counter()
    print(f"[node] score_node running (run_id={state['run_id']})")

    return {
        "scored_items": [],
        "stage": "scored",
        "costs": [_make_cost_record("score_node", t0)],
    }


def build_discovery_subgraph():
    """Compile the discovery subgraph: ingest_bookmarks -> cluster_dedupe -> score."""
    builder = StateGraph(DiscoverySubgraphState)

    builder.add_node("ingest_bookmarks", ingest_bookmarks)
    builder.add_node("cluster_dedupe", cluster_dedupe_node)
    builder.add_node("score", score_node)

    builder.add_edge(START, "ingest_bookmarks")
    builder.add_edge("ingest_bookmarks", "cluster_dedupe")
    builder.add_edge("cluster_dedupe", "score")
    builder.add_edge("score", END)

    return builder.compile()


def make_initial_state() -> DiscoverySubgraphState:
    """Helper to build a fresh, valid initial state for a run."""
    return DiscoverySubgraphState(
        raw_items=[],
        clustered_items=[],
        scored_items=[],
        run_id=str(uuid.uuid4())[:8],
        stage="start",
        costs=[],
        errors=[],
    )