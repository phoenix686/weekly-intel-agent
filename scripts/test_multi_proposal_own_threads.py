r"""
Tests whether a parent graph, fanning out via Send to N proposal_worker
tasks -- each of which invokes ITS OWN dedicated-thread child graph --
completes normally itself, while each child sits independently paused,
resumable later with zero need to touch the parent thread again.

Run: uv run --env-file .env python scripts/test_multi_proposal_own_threads.py
Then resume each independently:
  uv run --env-file .env python scripts/test_per_proposal_thread.py resume <thread_id>

Expected output:
dedicated-thread design works across separate process invocations.
[SEND] prop-C -> msg-76165
[SEND] prop-B -> msg-65509
[SEND] prop-A -> msg-24970
[proposal_worker] prop-C: child invoke returned, paused=True
[proposal_worker] prop-B: child invoke returned, paused=True
[proposal_worker] prop-A: child invoke returned, paused=True

=== PARENT RESULT ===
Parent has its own __interrupt__? False
Pending resumes captured: 3
  {'proposal_id': 'prop-A', 'thread_id': 'proposal-02380be9d2bcd116', 'message_id': 'msg-24970', 'paused': True}
  {'proposal_id': 'prop-B', 'thread_id': 'proposal-20da8c2a5d5953dc', 'message_id': 'msg-65509', 'paused': True}
  {'proposal_id': 'prop-C', 'thread_id': 'proposal-db869799736b4288', 'message_id': 'msg-76165', 'paused': True}

=== VERDICT ===
PASS — parent completed normally, all 3 proposals independently paused.
Each can now be resumed separately with test_per_proposal_thread.py resume <thread_id>
(.venv) PS C:\Users\Pooja\Documents\weekly-intel\langgraph-weekly-intel> 
"""

import hashlib
from typing import TypedDict, Annotated
from operator import add

from langgraph.graph import StateGraph, START, END
from langgraph.types import interrupt, Send, Command
from checkpointer_config import get_checkpointer


class ProposalState(TypedDict):
    proposal_id: str
    decision: str | None
    message_id: str | None


def send_proposal_message(state: ProposalState) -> dict:
    fake_message_id = f"msg-{hash(state['proposal_id']) % 100000}"
    print(f"[SEND] {state['proposal_id']} -> {fake_message_id}")
    return {"message_id": fake_message_id}


def await_confirmation(state: ProposalState) -> dict:
    decision = interrupt({"proposal_id": state["proposal_id"]})
    return {"decision": decision}


def thread_id_for(proposal_id: str) -> str:
    return "proposal-" + hashlib.sha256(proposal_id.encode()).hexdigest()[:16]


_child_graph = None
def get_child_graph():
    global _child_graph
    if _child_graph is None:
        g = StateGraph(ProposalState)
        g.add_node("send_proposal_message", send_proposal_message)
        g.add_node("await_confirmation", await_confirmation)
        g.add_edge(START, "send_proposal_message")
        g.add_edge("send_proposal_message", "await_confirmation")
        g.add_edge("await_confirmation", END)
        _child_graph = g.compile(checkpointer=get_checkpointer())
    return _child_graph


# --- Parent graph ---

class ParentState(TypedDict):
    proposals: list[str]
    pending_resumes: Annotated[list[dict], add]  # {proposal_id, thread_id, message_id}


def route(state: ParentState) -> list[Send]:
    return [Send("proposal_worker", {"proposal_id": pid, "decision": None, "message_id": None})
            for pid in state["proposals"]]


def proposal_worker(state: ProposalState) -> dict:
    thread_id = thread_id_for(state["proposal_id"])
    child = get_child_graph()
    result = child.invoke(state, config={"configurable": {"thread_id": thread_id}})

    paused = "__interrupt__" in result
    print(f"[proposal_worker] {state['proposal_id']}: child invoke returned, paused={paused}")

    return {
        "pending_resumes": [{
            "proposal_id": state["proposal_id"],
            "thread_id": thread_id,
            "message_id": result.get("message_id"),
            "paused": paused,
        }]
    }


graph = StateGraph(ParentState)
graph.add_node("proposal_worker", proposal_worker)
graph.add_conditional_edges(START, route)
graph.add_edge("proposal_worker", END)
compiled = graph.compile(checkpointer=get_checkpointer())

config = {"configurable": {"thread_id": "parent-multi-test-1"}}
result = compiled.invoke({"proposals": ["prop-A", "prop-B", "prop-C"], "pending_resumes": []}, config=config)

print("\n=== PARENT RESULT ===")
print(f"Parent has its own __interrupt__? {'__interrupt__' in result}")
print(f"Pending resumes captured: {len(result['pending_resumes'])}")
for pr in result["pending_resumes"]:
    print(f"  {pr}")

print("\n=== VERDICT ===")
if "__interrupt__" not in result and len(result["pending_resumes"]) == 3:
    print("PASS — parent completed normally, all 3 proposals independently paused.")
    print("Each can now be resumed separately with test_per_proposal_thread.py resume <thread_id>")
else:
    print("FAIL — parent did not complete cleanly, or not all proposals were captured.")