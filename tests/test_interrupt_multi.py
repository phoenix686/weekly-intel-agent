"""
Isolated test — proves, before real await_approval.py gets built:

1. Send-based fan-out to a SUBGRAPH per proposal avoids the shared-state
   concurrent-write conflict (InvalidUpdateError) that a flat two-node
   chain in the parent graph hit — each subgraph invocation has fully
   private state, so per-proposal fields like proposal_id never collide
   across parallel tasks. Only the final "results" list gets merged back
   into the parent via a reducer.
2. Splitting "send message" and "wait for approval" into two nodes WITHIN
   that subgraph means the send fires exactly once, even when other
   proposals' interrupts are resumed later.
3. Whether your installed LangGraph version hits a known, recent bug
   (langchain-ai/langgraph#6626, Dec 2025) where parallel interrupt() calls
   in the same checkpoint namespace get assigned IDENTICAL interrupt IDs.
   This test checks for that directly.

Run: uv run --env-file .env python scripts/test_interrupt_multi.py

Expected Output:
=== First invoke (should pause on all 3 proposals) ===
[SEND] proposal_id=prop-A (send #1 for this proposal)
[AWAIT] pausing for proposal_id=prop-A
[SEND] proposal_id=prop-B (send #1 for this proposal)
[AWAIT] pausing for proposal_id=prop-B
[SEND] proposal_id=prop-C (send #1 for this proposal)
[AWAIT] pausing for proposal_id=prop-C
Pending interrupts: 3
  id=1a045fe32f69fb0b48cad6859ea7c24c value={'proposal_id': 'prop-A', 'question': 'approve or reject?'}
  id=4bbbadc6a3b39b66767791a0b66b80e0 value={'proposal_id': 'prop-B', 'question': 'approve or reject?'}
  id=6861d5993cfa96c4bf1f65da5f777cbf value={'proposal_id': 'prop-C', 'question': 'approve or reject?'}

Unique interrupt IDs: 3 (expected 3)

=== Resuming all 3 with distinct per-proposal values (dict-based resume) ===
[AWAIT] pausing for proposal_id=prop-A
[RESUMED] proposal_id=prop-A decision=approve
[AWAIT] pausing for proposal_id=prop-B
[RESUMED] proposal_id=prop-B decision=reject
[AWAIT] pausing for proposal_id=prop-C
[RESUMED] proposal_id=prop-C decision=approve

Final results: [{'proposal_id': 'prop-A', 'decision': 'approve'}, {'proposal_id': 'prop-B', 'decision': 'reject'}, {'proposal_id': 'prop-C', 'decision': 'approve'}]

=== Send counts per proposal (must all be 1 — any 2+ is a real bug) ===
  prop-A: 1 (OK)
  prop-B: 1 (OK)
  prop-C: 1 (OK)

=== Result correctness check ===
  prop-A: got 'approve' (OK)
  prop-B: got 'reject' (OK)
  prop-C: got 'approve' (OK)

=== VERDICT ===
PASS — safe to build the real await_approval.py on this pattern.
"""

from typing import TypedDict, Annotated
from operator import add

from langgraph.graph import StateGraph, START, END
from langgraph.types import interrupt, Command, Send
from core.checkpointer_config import get_checkpointer

SEND_COUNTS: dict[str, int] = {}


# --- Per-proposal subgraph: fully private state, no sharing with siblings ---

class ProposalState(TypedDict):
    proposal_id: str
    decision: str | None


def send_proposal_message(state: ProposalState) -> dict:
    pid = state["proposal_id"]
    SEND_COUNTS[pid] = SEND_COUNTS.get(pid, 0) + 1
    print(f"[SEND] proposal_id={pid} (send #{SEND_COUNTS[pid]} for this proposal)")
    return {}


def await_confirmation(state: ProposalState) -> dict:
    pid = state["proposal_id"]
    print(f"[AWAIT] pausing for proposal_id={pid}")
    decision = interrupt({"proposal_id": pid, "question": "approve or reject?"})
    print(f"[RESUMED] proposal_id={pid} decision={decision}")
    return {"decision": decision}


proposal_subgraph = StateGraph(ProposalState)
proposal_subgraph.add_node("send_proposal_message", send_proposal_message)
proposal_subgraph.add_node("await_confirmation", await_confirmation)
proposal_subgraph.add_edge(START, "send_proposal_message")
proposal_subgraph.add_edge("send_proposal_message", "await_confirmation")
proposal_subgraph.add_edge("await_confirmation", END)
compiled_proposal_subgraph = proposal_subgraph.compile()


# --- Parent graph: fans out via Send, collects results via reducer ---

class TestState(TypedDict):
    proposals: list[str]
    results: Annotated[list[dict], add]


def route_to_proposals(state: TestState) -> list[Send]:
    return [
        Send("proposal_worker", {"proposal_id": pid, "decision": None})
        for pid in state["proposals"]
    ]


def proposal_worker(state: ProposalState) -> dict:
    # Runs the subgraph for exactly one proposal, then reports its result
    # back to the parent's reducer-based "results" field.
    final = compiled_proposal_subgraph.invoke(state)
    return {"results": [{"proposal_id": final["proposal_id"], "decision": final["decision"]}]}


graph = StateGraph(TestState)
graph.add_node("proposal_worker", proposal_worker)
graph.add_conditional_edges(START, route_to_proposals)
graph.add_edge("proposal_worker", END)

compiled = graph.compile(checkpointer=get_checkpointer())
config = {"configurable": {"thread_id": "multi-proposal-test-3"}}

print("=== First invoke (should pause on all 3 proposals) ===")
result = compiled.invoke({"proposals": ["prop-A", "prop-B", "prop-C"], "results": []}, config=config)

interrupts = result.get("__interrupt__", [])
print(f"Pending interrupts: {len(interrupts)}")
for i in interrupts:
    print(f"  id={i.id} value={i.value}")

interrupt_ids = [i.id for i in interrupts]
unique_ids = set(interrupt_ids)
print(f"\nUnique interrupt IDs: {len(unique_ids)} (expected 3)")
if len(unique_ids) < len(interrupt_ids):
    print("!!! ID COLLISION DETECTED — matches known bug langgraph#6626.")
    print("!!! Per-proposal resume will NOT work correctly on this version.")
    raise SystemExit(1)

print("\n=== Resuming all 3 with distinct per-proposal values (dict-based resume) ===")
expected_decisions = {"prop-A": "approve", "prop-B": "reject", "prop-C": "approve"}
resume_map = {i.id: expected_decisions[i.value["proposal_id"]] for i in interrupts}

resumed = compiled.invoke(Command(resume=resume_map), config=config)
print("\nFinal results:", resumed.get("results"))

print("\n=== Send counts per proposal (must all be 1 — any 2+ is a real bug) ===")
all_ok = True
for pid, count in SEND_COUNTS.items():
    status = "OK" if count == 1 else "DUPLICATE SEND — BUG"
    if count != 1:
        all_ok = False
    print(f"  {pid}: {count} ({status})")

print("\n=== Result correctness check ===")
results_by_pid = {r["proposal_id"]: r["decision"] for r in resumed.get("results", [])}
for pid, expected in expected_decisions.items():
    actual = results_by_pid.get(pid)
    match = "OK" if actual == expected else f"MISMATCH (expected {expected})"
    print(f"  {pid}: got '{actual}' ({match})")

print("\n=== VERDICT ===")
if all_ok and results_by_pid == expected_decisions:
    print("PASS — safe to build the real await_approval.py on this pattern.")
else:
    print("FAIL — do not proceed to real await_approval.py; investigate above first.")