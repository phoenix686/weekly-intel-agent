"""
Tests a DIFFERENT mechanism than the already-verified test_interrupt_multi.py.

That earlier test proved: proposals fanned out via Send, sharing ONE outer
thread_id, each getting a unique interrupt_id, resumed via a dict.

This test checks whether giving each proposal its OWN dedicated checkpointer
+ thread_id (invoked manually via .invoke() from inside proposal_worker)
allows:
1. message_id to be captured and persisted the MOMENT send happens --
   before any interrupt_id exists -- solving the temporal ordering problem
   found in the Phase 5A design discussion.
2. Simple single-value Command(resume="approve") to work, since each
   proposal's thread only ever has ONE pending interrupt.
3. The pause to genuinely survive across SEPARATE script invocations (not
   just separate .invoke() calls in the same process) -- simulating the
   real gap between "Sunday run sends proposal" and "poller resumes it,
   possibly hours later, in a completely different process."

Run first: uv run --env-file .env python scripts/test_per_proposal_thread.py send
Then separately: uv run --env-file .env python scripts/test_per_proposal_thread.py resume <thread_id>

expected output:
(.venv) PS C:\Users\Pooja\Documents\weekly-intel\langgraph-weekly-intel> uv run --env-file .env python scripts/test_per_proposal_thread.py resume proposal-4233d6cb7ac1718c
[AWAIT] pausing for proposal_id=test-proposal-XYZ
[RESUMED] decision=approve

=== RESUMED (fresh process) ===
{'proposal_id': 'test-proposal-XYZ', 'decision': 'approve', 'message_id': 'msg-40267'}

VERDICT: if 'decision': 'approve' appears above, the per-proposal
"""

import sys
import hashlib
from typing import TypedDict
from langgraph.graph import StateGraph, START, END
from langgraph.types import interrupt, Command
from checkpointer_config import get_checkpointer


class ProposalState(TypedDict):
    proposal_id: str
    decision: str | None
    message_id: str | None  # simulated -- would be real Telegram message_id


def send_proposal_message(state: ProposalState) -> dict:
    # Simulated Telegram send -- in reality, capture the real API response's message_id here
    fake_message_id = f"msg-{hash(state['proposal_id']) % 100000}"
    print(f"[SEND] proposal_id={state['proposal_id']} -> message_id={fake_message_id}")
    # THIS is the key moment: message_id is known here, BEFORE interrupt_id exists.
    # In the real implementation, persist {message_id: thread_id} to the store HERE.
    return {"message_id": fake_message_id}


def await_confirmation(state: ProposalState) -> dict:
    print(f"[AWAIT] pausing for proposal_id={state['proposal_id']}")
    decision = interrupt({"proposal_id": state["proposal_id"]})
    print(f"[RESUMED] decision={decision}")
    return {"decision": decision}


def build_proposal_graph():
    g = StateGraph(ProposalState)
    g.add_node("send_proposal_message", send_proposal_message)
    g.add_node("await_confirmation", await_confirmation)
    g.add_edge(START, "send_proposal_message")
    g.add_edge("send_proposal_message", "await_confirmation")
    g.add_edge("await_confirmation", END)
    # KEY DIFFERENCE from the verified design: this subgraph gets its OWN
    # checkpointer, not none/parent-inherited.
    return g.compile(checkpointer=get_checkpointer())


def thread_id_for(proposal_id: str) -> str:
    return "proposal-" + hashlib.sha256(proposal_id.encode()).hexdigest()[:16]


if __name__ == "__main__":
    graph = build_proposal_graph()
    mode = sys.argv[1] if len(sys.argv) > 1 else "send"

    if mode == "send":
        proposal_id = "test-proposal-XYZ"
        thread_id = thread_id_for(proposal_id)
        config = {"configurable": {"thread_id": thread_id}}

        result = graph.invoke({"proposal_id": proposal_id, "decision": None, "message_id": None}, config=config)
        interrupts = result.get("__interrupt__", [])
        print(f"\nPaused. thread_id={thread_id}")
        print(f"Interrupt: {interrupts}")
        print(f"\nRun again to resume: python scripts/test_per_proposal_thread.py resume {thread_id}")

    elif mode == "resume":
        thread_id = sys.argv[2]
        config = {"configurable": {"thread_id": thread_id}}
        # THE THING BEING TESTED: does a plain single-value resume work,
        # since this thread only ever has ONE pending interrupt?
        final = graph.invoke(Command(resume="approve"), config=config)
        print(f"\n=== RESUMED (fresh process) ===")
        print(final)
        print("\nVERDICT: if 'decision': 'approve' appears above, the per-proposal")
        print("dedicated-thread design works across separate process invocations.")