import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from state import NodeCost


def test_nodecost_constructs_with_error_set():
    cost = NodeCost(
        node_name="test_node", input_tokens=10, output_tokens=5,
        latency_ms=1.0, cost_usd=0.001, error="something went wrong",
    )
    assert cost["error"] == "something went wrong"


def test_nodecost_constructs_without_error_defaulting_absent():
    """Every pre-existing NodeCost(...) call site in the codebase omits
    error -- confirms the field is optional, not a breaking schema change."""
    cost = NodeCost(
        node_name="test_node", input_tokens=0, output_tokens=0,
        latency_ms=0.0, cost_usd=0.0,
    )
    assert "error" not in cost
    assert cost.get("error") is None
