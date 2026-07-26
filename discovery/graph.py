"""
Discovery subgraph.

route_sources is a real LangGraph conditional entry point
(StateGraph.set_conditional_entry_point()), per phase-5b-spec.md Section
2's locked design: ONE subgraph, not two variants -- adding a future
source means adding one node + one routing-list entry, not touching two
separate graph builders. This replaces an earlier, undocumented
build_discovery_subgraph(include_saturday_only) two-graph-shape pattern
that predated (and diverged from) that locked design; see phase-5b-spec.md
Section 10 for the reconciliation.

cluster_dedupe_node applies the cross-run dedup check (discovery/seen_items.py)
before score_node's paid Haiku call.

ingest_bookmarks is intentionally NOT a node in this graph at all -- it's
a manual-bootstrap-only source, invoked directly (outside any graph) by
scripts/save_clustered.py. Wiring it into a scheduled run caused a real
production FileNotFoundError on data/tweets.json (gitignored, doesn't
exist in a fresh checkout) before this fix.

search_web is retired entirely (batch2-dedup-taste-spec.md Section 6,
2026-07-16): discovery/nodes/search_web.py and
discovery/parsers/search_web.py deleted. blog_sources.yaml's live-verified
sources cover the same ground with better signal and lower cost -- no
distinct remaining job for it.

scrape_blogs is now the ONLY source node routed here (besides
process_adhoc_input, saturday-only) -- TLDR AI, Smol AI News, and
Anthropic's dev blog used to be separate dedicated node files
(tldr_ai.py, smol_ai_news.py, anthropic_blog.py); now that
discovery/config/blog_sources.yaml exists as the single source-of-truth
config, scrape_blogs reads it directly and fetches whatever's active for
the current invocation's cadence bucket -- see
discovery/parsers/scrape_blogs.py. Smol AI News was removed entirely
(not folded in), per explicit instruction.
"""

from __future__ import annotations

import uuid
from langgraph.graph import StateGraph, START, END
from core.state import DiscoverySubgraphState
from discovery.nodes.cluster_dedupe import cluster_dedupe_node
from discovery.nodes.score import score_node
from discovery.nodes.scrape_blogs import scrape_blogs
from saturday.nodes.process_adhoc_input import process_adhoc_input

# Every source node that's part of the scheduled discovery subgraph.
# ingest_bookmarks deliberately excluded -- see module docstring.
_ALL_SOURCE_NODES = {
    "scrape_blogs": scrape_blogs,
    "process_adhoc_input": process_adhoc_input,
}

# Active node names per invocation context. The single source of truth
# route_sources reads from -- a new source means one new row in
# discovery/config/blog_sources.yaml, not a new node here.
_DAILY_ACTIVE = ["scrape_blogs"]
_SATURDAY_ACTIVE = ["scrape_blogs", "process_adhoc_input"]


def route_sources(state: DiscoverySubgraphState) -> list[str]:
    """Conditional entry point: returns the active source node(s) for this
    invocation, read from state["source_context"] ("daily" or "saturday",
    set by make_daily_initial_state()/make_saturday_initial_state() and
    passed through to this subgraph by name intersection)."""
    context = state["source_context"]
    if context == "daily":
        return _DAILY_ACTIVE
    if context == "saturday":
        return _SATURDAY_ACTIVE
    raise ValueError(f"Unknown source_context: {context!r} (expected 'daily' or 'saturday')")


def build_discovery_subgraph():
    """Compile the discovery subgraph: route_sources (conditional entry
    point) -> [active source nodes for this invocation] -> cluster_dedupe
    -> score.

    daily/graph.py and saturday/graph.py both invoke this SAME compiled
    subgraph -- which source nodes actually run is decided entirely at
    runtime by route_sources reading state["source_context"], not by
    building two different graphs.
    """
    builder = StateGraph(DiscoverySubgraphState)

    builder.add_node("cluster_dedupe", cluster_dedupe_node)
    builder.add_node("score", score_node)

    for name, fn in _ALL_SOURCE_NODES.items():
        builder.add_node(name, fn)
        builder.add_edge(name, "cluster_dedupe")

    builder.set_conditional_entry_point(route_sources, list(_ALL_SOURCE_NODES.keys()))

    builder.add_edge("cluster_dedupe", "score")
    builder.add_edge("score", END)

    return builder.compile()


def make_initial_state(source_context: str = "daily", dry_run: bool = False) -> DiscoverySubgraphState:
    """Helper to build a fresh, valid initial state for a standalone run."""
    return DiscoverySubgraphState(
        raw_items=[],
        clustered_items=[],
        scored_items=[],
        run_id=str(uuid.uuid4())[:8],
        costs=[],
        errors=[],
        source_context=source_context,
        dry_run=dry_run,
    )
