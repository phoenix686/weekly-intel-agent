"""
Real (non-mocked) regression test for the ingest-bookmarks-gating bug:
scripts/run_daily.py's build_daily_graph() -- and sunday's build_sunday_graph()
-- must never include ingest_bookmarks as a node, since it reads
data/tweets.json, which is gitignored and does not exist in a fresh
Actions checkout. A prior mocked test of build_discovery_subgraph() alone
passed while the real entrypoints (which nest it inside a compiled
"discovery_subgraph" node) still shipped the bug -- these tests build the
actual graphs daily.yml/sunday.yml invoke, no mocks.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from daily.graph import build_daily_graph
from sunday.graph import build_sunday_graph
from discovery.graph import build_discovery_subgraph


def _discovery_subgraph_nodes(compiled_parent_graph):
    """The parent graph's get_graph() collapses the discovery subgraph into
    a single 'discovery_subgraph' node -- inspect the nested subgraph's own
    node set via xray to see what's actually inside it."""
    return compiled_parent_graph.get_graph(xray=True).nodes


def test_real_build_daily_graph_excludes_ingest_bookmarks():
    graph = build_daily_graph().compile()
    nodes = _discovery_subgraph_nodes(graph)
    assert "ingest_bookmarks" not in nodes, (
        "ingest_bookmarks must not be a node reachable from build_daily_graph() -- "
        "it reads data/tweets.json, which does not exist in a fresh Actions checkout."
    )


def test_real_build_sunday_graph_excludes_ingest_bookmarks():
    graph = build_sunday_graph()
    nodes = _discovery_subgraph_nodes(graph)
    assert "ingest_bookmarks" not in nodes, (
        "ingest_bookmarks must not be a node reachable from build_sunday_graph() either -- "
        "checkpoint 1's gating spec excludes it from BOTH scheduled contexts."
    )


def test_build_discovery_subgraph_excludes_ingest_bookmarks_both_modes():
    """discovery/graph.py now uses a single subgraph with a real
    route_sources() conditional entry point (not the earlier
    build_discovery_subgraph(include_sunday_only) two-graph-shape
    pattern) -- ingest_bookmarks is simply never registered as a node at
    all, so it's absent regardless of source_context."""
    all_nodes = build_discovery_subgraph().get_graph().nodes
    assert "ingest_bookmarks" not in all_nodes


def test_ingest_bookmarks_remains_directly_importable_for_manual_bootstrap():
    """Not deleted -- scripts/save_clustered.py's manual-bootstrap path calls
    this function directly, outside any graph."""
    from discovery.nodes.ingest_bookmarks import ingest_bookmarks
    assert callable(ingest_bookmarks)
