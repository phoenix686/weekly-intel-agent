"""
Discovery subgraph — Phase 1.

search_scrape_node (Phase 0 placeholder) is replaced by the real
ingest_bookmarks node. cluster_dedupe_node and score_node remain Phase 0
no-op placeholders until Phase 2.
"""

from __future__ import annotations

import uuid
from langgraph.graph import StateGraph, START, END
from state import DiscoverySubgraphState
from discovery.nodes.ingest_bookmarks import ingest_bookmarks
from discovery.nodes.cluster_dedupe import cluster_dedupe_node
from discovery.nodes.score import score_node


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