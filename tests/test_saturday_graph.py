import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unittest.mock import patch

import saturday.graph as saturday_graph


class _SpyStateGraph:
    def __init__(self, state_type):
        self.state_type = state_type
        self.nodes = {}
        self.edges = []
        self.conditional_edges = []

    def add_node(self, name, func):
        self.nodes[name] = func

    def add_edge(self, source, target):
        self.edges.append((source, target))

    def add_conditional_edges(self, source, path):
        self.conditional_edges.append((source, path))

    def compile(self, checkpointer=None):
        return self


def test_saturday_graph_runs_update_profile_after_plan_delivery():
    with patch("saturday.graph.StateGraph", _SpyStateGraph), \
         patch("saturday.graph.build_discovery_subgraph", return_value=object()), \
         patch("saturday.graph.get_checkpointer", return_value=None):
        graph = saturday_graph.build_saturday_graph()

    assert "update_profile" in graph.nodes
    assert ("send_telegram_plan", "update_profile") in graph.edges
    assert ("update_profile", saturday_graph.END) in graph.edges
    assert ("send_telegram_plan", saturday_graph.END) not in graph.edges
