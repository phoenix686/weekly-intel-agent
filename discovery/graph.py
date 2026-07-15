"""
Discovery subgraph.

cluster_dedupe_node applies Part A's cross-run dedup check before
score_node's paid Haiku call.

Source nodes fan out from START and converge into cluster_dedupe (a
standard LangGraph join -- cluster_dedupe only runs once all of its
incoming source-node edges have completed for that invocation), all
writing to the shared raw_items reducer.

search_web is NOT wired in: X integration is explicitly out of scope for
this phase, and its parser is still an unconfigured NotImplementedError
stub -- wiring it in would break every discovery run.

ingest_bookmarks is NOT wired into either scheduled invocation (bug fix,
Checkpoint 3 follow-up): it reads data/tweets.json, which is gitignored
and does not exist in a fresh checkout, so a scheduled Actions run
(daily.yml or sunday.yml) hit an uncaught FileNotFoundError in
production. Per the original ingest-bookmarks-gating spec, it's a
one-time manual-bootstrap source, not a scheduled one -- scripts/save_clustered.py
already serves as that manual invocation path, calling the node function
directly outside the graph. It remains importable/callable, just not a
node in either build_discovery_subgraph() output.
"""

from __future__ import annotations

import uuid
from langgraph.graph import StateGraph, START, END
from state import DiscoverySubgraphState
from discovery.nodes.cluster_dedupe import cluster_dedupe_node
from discovery.nodes.score import score_node
from discovery.nodes.tldr_ai import tldr_ai
from discovery.nodes.smol_ai_news import smol_ai_news
from discovery.nodes.hacker_news import hacker_news
from discovery.nodes.discovered_sources import discovered_daily_sources, discovered_sunday_sources
from discovery.nodes.scrape_blogs import scrape_blogs
from discovery.nodes.anthropic_blog import anthropic_blog
from sunday.nodes.process_adhoc_input import process_adhoc_input

# Included in BOTH daily and Sunday discovery subgraph invocations.
# ingest_bookmarks is deliberately NOT here -- see module docstring.
DAILY_SOURCE_NODES = {
    "tldr_ai": tldr_ai,
    "smol_ai_news": smol_ai_news,
    "hacker_news": hacker_news,
    "discovered_daily_sources": discovered_daily_sources,
}

# Sunday-only.
SUNDAY_ONLY_SOURCE_NODES = {
    "scrape_blogs": scrape_blogs,
    "anthropic_blog": anthropic_blog,
    "process_adhoc_input": process_adhoc_input,
    "discovered_sunday_sources": discovered_sunday_sources,
}


def build_discovery_subgraph(include_sunday_only: bool = False):
    """Compile the discovery subgraph: [source nodes, fanned out from
    START] -> cluster_dedupe -> score.

    include_sunday_only=False (daily/graph.py): tldr_ai, smol_ai_news,
    hacker_news, discovered_daily_sources.
    include_sunday_only=True (sunday/graph.py): all of the above PLUS
    scrape_blogs, anthropic_blog, process_adhoc_input,
    discovered_sunday_sources.

    ingest_bookmarks is never included here -- it's a manual-bootstrap
    source, invoked directly (not via this graph) by scripts/save_clustered.py.
    """
    builder = StateGraph(DiscoverySubgraphState)

    source_nodes = dict(DAILY_SOURCE_NODES)
    if include_sunday_only:
        source_nodes.update(SUNDAY_ONLY_SOURCE_NODES)

    builder.add_node("cluster_dedupe", cluster_dedupe_node)
    builder.add_node("score", score_node)

    for name, fn in source_nodes.items():
        builder.add_node(name, fn)
        builder.add_edge(START, name)
        builder.add_edge(name, "cluster_dedupe")

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
