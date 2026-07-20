"""
Isolated test — does a downstream node with multiple incoming edges
(one static edge + N dynamic Send-dispatched edges) fire ONCE after all
branches complete, or once PER branch?

This directly answers the update_profile question for sunday/graph.py
before it gets built for real.

Run: uv run --env-file .env python scripts/test_fan_in.py

Expected Output:
(.venv)> uv run --env-file .env python scripts/test_fan_in.py
[plan_branch] running
[proposal_branch] running for A
[proposal_branch] running for B
[proposal_branch] running for C
[fan_in_node] CALL #1 — sees branch_results so far: ['plan', 'proposal:A', 'proposal:B', 'proposal:C']

=== VERDICT ===
fan_in_node was called 1 time(s).
Final branch_results: ['plan', 'proposal:A', 'proposal:B', 'proposal:C']
RESULT: Fires ONCE after all branches complete — no code change needed.
update_profile can be wired exactly as currently drafted; costs will be
the full accurate per-run total, not a partial one.
"""

from typing import TypedDict, Annotated
from operator import add

from langgraph.graph import StateGraph, START, END
from langgraph.types import Send
from langgraph.checkpoint.memory import InMemorySaver  # no interrupt here, in-memory is fine

FAN_IN_CALL_COUNT = 0


class TestState(TypedDict):
    proposals: list[str]
    branch_results: Annotated[list[str], add]


def plan_branch(state: TestState) -> dict:
    print("[plan_branch] running")
    return {"branch_results": ["plan"]}


def proposal_branch(state: TestState) -> dict:
    pid = state["proposal_id"]
    print(f"[proposal_branch] running for {pid}")
    return {"branch_results": [f"proposal:{pid}"]}


def fan_in_node(state: TestState) -> dict:
    global FAN_IN_CALL_COUNT
    FAN_IN_CALL_COUNT += 1
    print(f"[fan_in_node] CALL #{FAN_IN_CALL_COUNT} — sees branch_results so far: {state['branch_results']}")
    return {}


def route(state: TestState) -> list[Send]:
    sends = [Send("plan_branch", state)]
    sends += [Send("proposal_branch", {**state, "proposal_id": pid}) for pid in state["proposals"]]
    return sends


graph = StateGraph(TestState)
graph.add_node("plan_branch", plan_branch)
graph.add_node("proposal_branch", proposal_branch)
graph.add_node("fan_in_node", fan_in_node)
graph.add_conditional_edges(START, route)
graph.add_edge("plan_branch", "fan_in_node")
graph.add_edge("proposal_branch", "fan_in_node")
graph.add_edge("fan_in_node", END)

compiled = graph.compile(checkpointer=InMemorySaver())
config = {"configurable": {"thread_id": "fan-in-test-1"}}

result = compiled.invoke({"proposals": ["A", "B", "C"], "branch_results": []}, config=config)

print(f"\n=== VERDICT ===")
print(f"fan_in_node was called {FAN_IN_CALL_COUNT} time(s).")
print(f"Final branch_results: {result['branch_results']}")

if FAN_IN_CALL_COUNT == 1:
    print("RESULT: Fires ONCE after all branches complete — no code change needed.")
    print("update_profile can be wired exactly as currently drafted; costs will be")
    print("the full accurate per-run total, not a partial one.")
else:
    print(f"RESULT: Fires {FAN_IN_CALL_COUNT} times — once per branch, as feared.")
    print("update_profile needs an explicit fan-in redesign before 4B.3.")